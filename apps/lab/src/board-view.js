"use strict";

// LED PWM channel values are linear light; CSS colors are encoded sRGB.
function ledDisplayColor(rgb) {
  const channel = shift => {
    const value = ((rgb >>> shift) & 255) / 255;
    return Math.round(255 * (value <= .0031308 ? 12.92 * value : 1.055 * value ** (1 / 2.4) - .055));
  };
  return `rgb(${channel(16)}, ${channel(8)}, ${channel(0)})`;
}

function ledSourceLabel(source) {
  return ({"ws2812-din": "WS2812 引脚波形", "ws2812-dma": "WS2812 DMA 波形", "gpio-latch": "GPIO 电平"})[source] || source;
}

// Presentation only: app.js owns the session, commands, and observed state.
class BoardView {
  constructor(activate, releaseKey = () => {}, changePose = () => {}) {
    this.activate = activate;
    this.releaseKey = releaseKey;
    this.changePose = changePose;
    this.mode = "3d";
    this.selected = null;
    this.components = [];
    this.zoom = 1;
    this.pan = {x: 0, y: 0};
    this.viewport = document.getElementById("board-viewport");
    this.scene = document.getElementById("board-scene");
    this.detail = document.getElementById("board-detail-state");
    this.pins = document.getElementById("board-pin-list");
    this.action = document.getElementById("board-detail-action");
    this.action.addEventListener("click", () => this.activate(this.selected, "action"));
    document.getElementById("board-detail-adc").addEventListener("click", () => this.activate(this.selected, "adc"));
    document.getElementById("board-zoom-in").addEventListener("click", () => this.mode === "3d" ? this.model?.zoom(.8) : this.setZoom(this.zoom + .25));
    document.getElementById("board-zoom-out").addEventListener("click", () => this.mode === "3d" ? this.model?.zoom(1.25) : this.setZoom(this.zoom - .25));
    document.getElementById("board-fit").addEventListener("click", () => this.fit());
    document.getElementById("board-top").addEventListener("click", () => this.model?.home("top"));
    document.getElementById("board-bottom").addEventListener("click", () => this.model?.home("bottom"));
    document.getElementById("board-mode").addEventListener("change", event => this.setMode(event.target.value));
    document.getElementById("board-interaction").addEventListener("change", () => this.updateInteraction());
    document.getElementById("board-level").addEventListener("click", () => this.changePose([0, 0, 0, 1]));
    document.getElementById("board-search").addEventListener("input", () => this.search());
    document.getElementById("board-search").addEventListener("keydown", event => {
      if (event.key === "ArrowDown") {
        document.querySelector("#board-search-results mdui-button")?.focus(); event.preventDefault();
      } else if (event.key === "Enter") document.querySelector("#board-search-results mdui-button")?.click();
      else if (event.key === "Escape") this.closeSearch();
    });
    document.getElementById("board-search-results").addEventListener("keydown", event => {
      const items = [...event.currentTarget.children], current = items.indexOf(event.target);
      if (["ArrowDown", "ArrowUp"].includes(event.key)) {
        items[Math.max(0, Math.min(items.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
        event.preventDefault();
      } else if (event.key === "Escape") { this.closeSearch(); document.getElementById("board-search").focus(); }
    });
    document.getElementById("board-locate").addEventListener("click", () => this.model?.locate(this.selected?.ref));
    window.addEventListener("pagehide", () => this.model?.dispose(), {once: true});
    new ResizeObserver(() => this.resize()).observe(this.viewport);
    let drag = null;
    this.viewport.addEventListener("pointerdown", (event) => {
      if (this.mode !== "2d" || event.button !== 0 || event.composedPath().some(node => node.tagName === "MDUI-BUTTON")) return;
      drag = {id: event.pointerId, x: event.clientX - this.pan.x, y: event.clientY - this.pan.y};
      this.viewport.setPointerCapture(event.pointerId);
      this.viewport.classList.add("dragging");
    });
    this.viewport.addEventListener("pointermove", (event) => {
      if (!drag || drag.id !== event.pointerId) return;
      this.pan = {x: event.clientX - drag.x, y: event.clientY - drag.y};
      this.transform();
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) this.viewport.addEventListener(name, () => {
      drag = null; this.viewport.classList.remove("dragging");
    });
    this.viewport.addEventListener("wheel", (event) => {
      if (this.mode !== "2d") return;
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      this.setZoom(this.zoom + (event.deltaY < 0 ? .25 : -.25));
    }, {passive: false});
    this.ready = this.load();
  }

  async load() {
    const response = await fetch("/board.svg");
    if (!response.ok) throw new Error(`板卡图加载失败（HTTP ${response.status}）`);
    // A packaged, fixed allowlisted asset; no user-supplied SVG is admitted.
    document.getElementById("board-art").innerHTML = await response.text();
    this.svg = document.querySelector("#board-art svg");
    const overlay = document.getElementById("board-hotspots");
    for (const group of this.svg.querySelectorAll("[data-component]")) {
      const component = {...group.dataset, description: group.querySelector("desc")?.textContent || ""};
      const box = group.getBBox();
      component.box = box;
      const held = ["key", "reset"].includes(component.kind);
      const button = held ? document.getElementById(`${component.kind}-button`) : document.createElement("mdui-button");
      button.id = held ? `${component.kind}-button` : `board-part-${component.component}`;
      button.className = "board-hotspot";
      button.variant = "text";
      button.textContent = component.label;
      button.title = component.label;
      button.setAttribute("aria-label", component.label);
      button.style.left = `${box.x / 1120 * 100}%`;
      button.style.top = `${box.y / 800 * 100}%`;
      button.style.width = `${box.width / 1120 * 100}%`;
      button.style.height = `${box.height / 800 * 100}%`;
      button.hidden = false;
      const select = () => {
        this.selected = component;
        for (const other of this.components) other.button.toggleAttribute("data-selected", other === component);
      };
      if (held) {
        button.addEventListener("focus", () => { select(); this.activate(component, "select"); });
        button.addEventListener("pointerdown", () => { select(); this.activate(component, "select"); });
      } else button.addEventListener("click", () => { select(); this.activate(component, "select"); });
      button.addEventListener("focus", () => this.reveal(component));
      component.button = button;
      this.components.push(component);
      overlay.append(button);
    }
    this.led = this.svg.querySelector("[data-led-core]");
    this.glow = this.svg.querySelector("[data-led-glow]");
    this.resize();
    await this.loadModel();
    document.getElementById("board-loading").hidden = true;
  }

  async loadModel() {
    const $ = id => document.getElementById(id);
    const fail = error => {
      $("board-model-error").textContent = `三维展示不可用：${error.message}`;
      $("board-model-error").hidden = false;
      $("board-mode-3d").disabled = true;
      this.setMode("2d");
    };
    try {
      const {BoardModel} = await import("./board-model.js");
      this.model = new BoardModel($("board-model"), {
        select: part => this.selectModel(part), frame: () => this.projectKey(), error: fail,
        pose: value => this.changePose(value),
      });
      this.setMode(this.mode);
      await this.model.load("/models/dm-mc02.glb");
      this.setMode(this.mode);
    } catch (error) { fail(error); }
  }

  selectModel(part, intent = "select") {
    if (!part) return;
    const controls = this.components.find(item => item.component === part.component);
    this.selected = { ...controls, component: controls?.component || part.ref,
      ref: part.ref, label: part.label || controls?.label || part.ref,
      description: part.description || controls?.description || part.ref,
      value: part.value, schematicPage: part.schematicPage };
    this.model.select(part.ref);
    document.getElementById("board-locate").disabled = false;
    this.activate(this.selected, intent);
  }

  closeSearch() {
    document.getElementById("board-search-results").hidden = true;
    document.getElementById("board-search-status").hidden = true;
  }

  search() {
    const query = document.getElementById("board-search").value.trim().toLowerCase();
    const results = document.getElementById("board-search-results"), status = document.getElementById("board-search-status");
    results.replaceChildren();
    if (!query || !this.model) { this.closeSearch(); return; }
    const parts = [...this.model.parts.values()].map(part => part.userData).filter(part => {
      const control = this.components.find(item => item.component === part.component);
      return [part.ref, part.label, part.value, control?.endpoint].filter(Boolean).join(" ").toLowerCase().includes(query);
    }).sort((a, b) => Number(b.ref.toLowerCase() === query) - Number(a.ref.toLowerCase() === query)
      || a.ref.localeCompare(b.ref, "en", {numeric: true}));
    for (const part of parts) {
      const button = document.createElement("mdui-button"); button.variant = "text"; button.dataset.ref = part.ref;
      button.textContent = `${part.ref} · ${part.label}${part.value ? ` · ${part.value}` : ""}`;
      button.addEventListener("click", () => { this.selectModel(part); this.closeSearch(); document.getElementById("board-locate").focus(); });
      results.append(button);
    }
    results.hidden = !parts.length; status.hidden = parts.length > 0;
    status.textContent = "没有匹配的元件";
  }

  setMode(mode) {
    this.releaseKey();
    this.mode = mode;
    const $ = id => document.getElementById(id), three = mode === "3d";
    $("board-mode").value = mode;
    $("board-model").hidden = !three; this.scene.hidden = three;
    $("board-part-search").hidden = !three;
    this.closeSearch();
    for (const id of ["board-top", "board-bottom", "board-motion-controls"]) $(id).hidden = !three;
    $("board-zoom").hidden = three;
    $("board-view-hint").textContent = three ? "拖动旋转 · 滚轮缩放 · 右键平移" : "俯视 · 56 × 40 mm · 拖动平移";
    this.viewport.setAttribute("aria-label", three ? "板卡三维展示" : "板卡俯视图，可拖动平移");
    this.model?.setVisible(three);
    this.updateInteraction();
    for (const component of this.components.filter(item => ["key", "reset"].includes(item.kind))) {
      const button = component.button;
      button.style.cssText = "";
      button.classList.toggle("board-key-3d", three);
      (three ? $("board-model") : $("board-hotspots")).append(button);
      button.hidden = three;
      if (!three) {
        const box = component.box;
        Object.assign(button.style, {left: `${box.x / 1120 * 100}%`, top: `${box.y / 800 * 100}%`,
          width: `${box.width / 1120 * 100}%`, height: `${box.height / 800 * 100}%`});
      }
    }
    this.resize();
    if (three) {
      const part = this.model?.parts.get(this.selected?.ref)
        || [...(this.model?.parts.values() || [])].find(item => item.userData.component && item.userData.component === this.selected?.component);
      this.model?.select(part?.userData.ref);
      $("board-zoom-in").disabled = false; $("board-zoom-out").disabled = false;
      this.projectKey();
    }
  }

  updateInteraction() {
    const mode = document.getElementById("board-interaction").value;
    this.model?.setInteraction(mode, this.motionEnabled);
    document.getElementById("board-level").disabled = !this.motionEnabled;
    if (this.mode === "3d") document.getElementById("board-view-hint").textContent = mode === "camera"
      ? "拖动转动视角 · 滚轮缩放 · 右键平移"
      : this.motionEnabled ? "拖动改变姿态 · 滚轮缩放 · 右键平移" : "启动姿态输入后，可拖动板子";
  }

  projectKey() {
    if (this.mode !== "3d") return;
    for (const [ref, id] of [["SW1", "key-button"], ["SW2", "reset-button"]]) {
      const point = this.model?.project(ref), key = document.getElementById(id);
      if (!point && !key.hidden) this.releaseKey();
      key.hidden = !point;
      if (point) { key.style.left = `${point.x}px`; key.style.top = `${point.y}px`; }
    }
  }

  resize() {
    if (this.mode === "3d") { this.model?.resize(); return; }
    if (!this.viewport.clientWidth || !this.viewport.clientHeight) return;
    this.width = Math.max(140, Math.min(this.viewport.clientWidth - 56, (this.viewport.clientHeight - 40) * 1.4));
    this.scene.style.width = `${this.width}px`;
    this.transform();
  }

  fit() {
    if (this.mode === "3d") { this.model?.home(); return; }
    this.zoom = 1; this.pan = {x: 0, y: 0}; this.resize();
  }

  setZoom(value) {
    this.zoom = Math.max(1, Math.min(3, value));
    if (this.zoom === 1) this.pan = {x: 0, y: 0};
    this.transform();
  }

  transform() {
    const limit = (this.width || 0) * this.zoom / 2;
    this.pan.x = Math.max(-limit, Math.min(limit, this.pan.x));
    this.pan.y = Math.max(-limit / 1.4, Math.min(limit / 1.4, this.pan.y));
    this.scene.style.transform = `translate(-50%, -50%) translate(${this.pan.x}px, ${this.pan.y}px) scale(${this.zoom})`;
    document.getElementById("board-zoom").textContent = `${Math.round(this.zoom * 100)}%`;
    document.getElementById("board-zoom-out").disabled = this.zoom === 1;
    document.getElementById("board-zoom-in").disabled = this.zoom === 3;
  }

  reveal(component) {
    if (this.mode === "3d") return;
    const rect = component.button.getBoundingClientRect(), port = this.viewport.getBoundingClientRect();
    if (rect.left >= port.left && rect.right <= port.right && rect.top >= port.top && rect.bottom <= port.bottom) return;
    this.pan = {x: (560 - component.box.x - component.box.width / 2) / 1120 * this.width * this.zoom,
      y: (400 - component.box.y - component.box.height / 2) / 1120 * this.width * this.zoom};
    this.transform();
  }

  update(state, connected, channels) {
    const $ = id => document.getElementById(id);
    const board = state.board;
    const active = ["running", "paused"].includes(state.status);
    const stale = !!board && (!connected || state.observation_stale || !active);
    this.motionEnabled = connected && !stale && state.status === "running" && state.motion?.mode === "board_pose";
    this.model?.observeMotion(state.motion);
    this.updateInteraction();
    $("board-motion-status").textContent = this.motionEnabled ? "姿态 → IMU" : state.motion?.mode === "stationary" ? "固定静止输入" : "";
    const observation = !connected ? "连接已断开" : !board ? "等待观测"
      : stale ? "历史快照 · 已过期" : state.status === "paused" ? "已暂停 · 保留观测" : "实时观测";
    $("board-observation").textContent = observation;
    $("console-board").dataset.observation = !board ? "unknown" : stale ? "stale" : "current";
    const rgb = board ? `#${board.led.rgb.toString(16).padStart(6, "0")}` : null;
    this.model?.updateLED(board ? board.led.rgb : null);
    if (this.led) {
      this.led.setAttribute("fill", board ? ledDisplayColor(board.led.rgb) : "#777e6c");
      this.led.setAttribute("aria-label", rgb ? `LED ${rgb.toUpperCase()}` : "LED 无数据");
      this.glow.setAttribute("fill", board ? ledDisplayColor(board.led.rgb) : "none");
      this.glow.style.display = board?.led.rgb ? "" : "none";
    }
    for (const component of this.components) {
      const enabled = component.endpoint ? channels[component.kind].includes(component.endpoint) : true;
      component.button.dataset.available = enabled ? "true" : "false";
      component.button.title = component.label + (enabled ? "" : " · 未启用");
    }
    const component = this.selected;
    $("board-detail-ref").textContent = component?.ref || "器件与接口";
    $("board-detail-title").textContent = component?.label || "喵板 · DM-MC02";
    $("board-detail-description").textContent = component?.description || "选择板上的器件或接口，查看状态与可用操作。";
    $("board-detail-note").textContent = component?.schematicPage
      ? `CtrBoard-H7 V1.0 · 原理图第 ${component.schematicPage} 页`
      : "选择接口后可打开通信面板。";
    let text;
    let action = "";
    if (component?.endpoint) {
      const enabled = channels[component.kind].includes(component.endpoint);
      text = enabled ? `${component.endpoint.toUpperCase()} · ${active ? "本次会话已启用" : "已配置，等待启动"}` : `${component.endpoint.toUpperCase()} · 未启用，在设置中启用后重新启动。`;
      action = enabled ? `打开 ${component.endpoint.toUpperCase()}` : "打开设置";
    } else if (component?.kind === "led") {
      text = board ? `${rgb.toUpperCase()}\n${ledSourceLabel(board.led.source)}` : "尚无 LED 观测";
    } else if (component?.kind === "key") {
      text = board ? (board.key.pressed ? "已按下" : "已释放") : "尚无按键观测";
    } else if (component?.kind === "reset") {
      text = board ? (board.reset?.pressed ? "保持复位" : "已释放") : "按住保持复位，松开后启动";
    } else if (component?.component === "imu" && state.motion?.mode === "board_pose") {
      text = `加速度 / m/s²\n${state.motion.accel_m_s2.map(v => v.toFixed(3)).join(" · ")}\n角速度 / rad/s\n${state.motion.gyro_rad_s.map(v => v.toFixed(3)).join(" · ")}`;
    } else if (component?.kind === "vin") {
      text = board ? `${board.vin_mv / 1000} V` : "尚无 VIN 观测"; action = "设置输入电压";
    } else if (component?.kind === "usb") {
      const usb = state.usb || {state: "disabled"};
      text = ({disabled: "未启用 USB/IP", disconnected: "未连接", enumerating: "枚举中", available: "可连接本机", attached: "已连接本机"})[usb.state] || usb.state;
      action = "USB 连接管理";
    } else if (component?.component === "mcu" || !component) {
      text = `${({idle: "未启动", starting: "启动中", running: "运行中", paused: "已暂停", stopping: "停止中", stopped: "已停止", failed: "失败"})[state.status] || state.status}\nVIN ${board ? `${board.vin_mv / 1000} V` : "—"} · LED ${rgb?.toUpperCase() || "—"}`;
    } else text = component.value || "";
    if ((!connected || stale) && (component?.kind || !component || component.component === "mcu")) text += `\n${observation}`;
    this.detail.textContent = text;
    this.detail.hidden = !text;
    if (this.action.textContent !== action) this.action.textContent = action;
    this.action.hidden = !action;
    $("board-detail-adc").hidden = component?.kind !== "adc";
    // Keep focused controls intact while repeated snapshots update their text.
    const pinNames = component?.pins?.split(" ").filter(Boolean) || [];
    const pinKey = pinNames.join(" ");
    if (this.pins.dataset.pins !== pinKey) {
      this.pins.replaceChildren(); this.pins.dataset.pins = pinKey;
      for (const pin of pinNames) {
        const row = document.createElement("div"); row.className = "board-pin";
        const button = document.createElement("mdui-button"); button.variant = "text"; button.textContent = `P${pin}`;
        button.setAttribute("aria-label", `查看 P${pin} 引脚`);
        button.addEventListener("click", () => this.activate({pin}, "gpio"));
        const value = document.createElement("span"); row.append(button, value); this.pins.append(row);
      }
    }
    pinNames.forEach((pin, index) => {
      const observed = state.pins?.find(item => item.pin === pin);
      this.pins.children[index].lastChild.textContent = observed ? `${["输入", "输出", "复用", "模拟"][observed.mode]} · ${observed.level}` : "无观测";
    });
  }
}
