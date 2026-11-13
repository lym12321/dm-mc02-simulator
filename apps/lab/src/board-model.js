import * as THREE from './vendor/three.js';

// Geometry and gestures. The native Lab owner computes motion and IMU samples.
export class BoardModel {
  constructor(host, {select = () => {}, frame = () => {}, error = () => {}, pose = () => {}} = {}) {
    this.host = host; this.onSelect = select; this.onFrame = frame; this.onError = error;
    this.onPose = pose; this.interaction = 'camera'; this.motionEnabled = false;
    this.bodyOrientation = new THREE.Quaternion(); this.viewFrame = new THREE.Quaternion();
    this.parts = new Map(); this.visible = true; this.disposed = false;
    this.pointer = new THREE.Vector2(); this.raycaster = new THREE.Raycaster();
    this.observer = new ResizeObserver(() => this.resize()); this.observer.observe(host);
    this.abort = new AbortController();
  }

  async load(source) {
    try {
      this.renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
      this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
      this.renderer.setClearColor(0x000000, 0);
      this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = 1.15;
      this.renderer.shadowMap.enabled = true;
      this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      const canvas = this.renderer.domElement;
      canvas.setAttribute('aria-label', '喵板三维模型；拖动旋转，滚轮缩放，方向键平移');
      canvas.tabIndex = 0; this.host.prepend(canvas);
      this.scene = new THREE.Scene();
      this.camera = new THREE.PerspectiveCamera(35, 1, .001, 2);
      const pmrem = new THREE.PMREMGenerator(this.renderer), room = new THREE.RoomEnvironment();
      this.environment = pmrem.fromScene(room, .04);
      this.scene.environment = this.environment.texture;
      this.scene.environmentIntensity = .6;
      room.dispose(); pmrem.dispose();
      const key = new THREE.DirectionalLight(0xfff6e7, 1.8); key.position.set(-.04, .1, .05);
      key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
      Object.assign(key.shadow.camera, {left: -.045, right: .045, top: .04, bottom: -.04, near: .01, far: .2});
      key.shadow.bias = -.0001; key.shadow.normalBias = .00003;
      const fill = new THREE.DirectionalLight(0xd9e8ff, .8); fill.position.set(.05, .025, -.06);
      this.scene.add(key, fill, new THREE.HemisphereLight(0xe5edff, 0x667065, .5));
      this.controls = new THREE.OrbitControls(this.camera, canvas);
      this.controls.minDistance = .012; this.controls.maxDistance = .3;
      this.controls.zoomSpeed = .7; this.controls.rotateSpeed = .7;
      this.controls.listenToKeyEvents(canvas);
      this.controls.addEventListener('change', () => this.invalidate());
      const listen = (target, name, callback) => target.addEventListener(name, callback, {signal: this.abort.signal});
      let down = null;
      listen(canvas, 'pointerdown', event => {
        down = {x: event.clientX, y: event.clientY, id: event.pointerId};
        if (event.button === 0 && this.interaction === 'body' && this.motionEnabled) {
          this.drag = {id: event.pointerId, x: event.clientX, y: event.clientY, target: this.bodyOrientation.clone()};
          canvas.setPointerCapture(event.pointerId);
        }
      });
      listen(canvas, 'pointerup', event => {
        if (event.button === 0 && down?.id === event.pointerId && Math.hypot(event.clientX - down.x, event.clientY - down.y) < 5) {
          const part = this.pick(event);
          if (part) { this.select(part.userData.ref); this.onSelect(part.userData); }
        }
        this.drag = null;
        if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
        down = null;
      });
      listen(canvas, 'pointercancel', () => { down = null; this.drag = null; });
      listen(canvas, 'lostpointercapture', () => { down = null; this.drag = null; });
      listen(canvas, 'pointermove', event => {
        if (this.drag?.id === event.pointerId) {
          const dx = event.clientX - this.drag.x, dy = event.clientY - this.drag.y;
          this.drag.x = event.clientX; this.drag.y = event.clientY;
          const axis = new THREE.Vector3(dy, dx, 0).applyQuaternion(this.camera.quaternion);
          if (axis.lengthSq()) {
            const delta = new THREE.Quaternion().setFromAxisAngle(axis.clone().normalize(), axis.length() * .008);
            const bodyDelta = this.viewFrame.clone().invert().multiply(delta).multiply(this.viewFrame);
            this.drag.target.premultiply(bodyDelta).normalize();
            this.onPose(this.drag.target.toArray());
          }
          return;
        }
        if (event.buttons) return;
        const part = this.pick(event);
        canvas.title = part ? `${part.userData.ref} · ${part.userData.label || '查看器件'}` : '';
      });
      listen(canvas, 'webglcontextlost', event => {
        event.preventDefault(); this.setVisible(false); this.onError(new Error('三维图形上下文已丢失，可使用二维视图；重新打开应用可恢复三维。'));
      });
      listen(document, 'visibilitychange', () => { if (!document.hidden) this.invalidate(); });
      const loader = new THREE.GLTFLoader().setMeshoptDecoder(THREE.MeshoptDecoder);
      const gltf = typeof source === 'string' ? await loader.loadAsync(source) : await loader.parseAsync(source, '');
      this.model = gltf.scene;
      this.model.traverse(node => {
        if (node.userData.ref) this.parts.set(node.userData.ref, node);
        if (node.isMesh) { node.castShadow = true; node.receiveShadow = true; }
      });
      this.body = new THREE.Group(); this.body.add(this.model); this.scene.add(this.body);
      this.bounds = new THREE.Box3().setFromObject(this.model);
      this.center = this.bounds.getCenter(new THREE.Vector3());
      const led = [...this.parts.values()].find(node => node.userData.component === 'led');
      this.ledMaterials = [];
      led?.traverse(node => {
        if (node.isMesh) {
          node.material = node.material.clone(); this.ledMaterials.push(node.material);
        }
      });
      if (led) {
        const box = new THREE.Box3().setFromObject(led), position = box.getCenter(new THREE.Vector3());
        position.y = box.max.y + .0003;
        this.ledLight = new THREE.PointLight(0xffffff, 0, .014, 2);
        this.ledLight.position.copy(position); this.body.add(this.ledLight);
        const glow = document.createElement('canvas'); glow.width = glow.height = 64;
        const context = glow.getContext('2d'), gradient = context.createRadialGradient(32, 32, 0, 32, 32, 32);
        gradient.addColorStop(0, 'rgba(255,255,255,.8)'); gradient.addColorStop(.25, 'rgba(255,255,255,.25)'); gradient.addColorStop(1, 'rgba(255,255,255,0)');
        context.fillStyle = gradient; context.fillRect(0, 0, 64, 64);
        this.ledHaloTexture = new THREE.CanvasTexture(glow);
        this.ledHalo = new THREE.Sprite(new THREE.SpriteMaterial({map: this.ledHaloTexture,
          transparent: true, depthWrite: false, toneMapped: false}));
        this.ledHalo.position.copy(position); this.ledHalo.scale.set(.006, .006, 1);
        this.body.add(this.ledHalo);
      }
      const rgb = this.rgb; this.rgb = undefined; this.updateLED(rgb ?? null);
      this.selection = new THREE.Box3Helper(new THREE.Box3(), 0x599b8b);
      this.selection.material.depthTest = false; this.selection.material.transparent = true;
      this.selection.material.opacity = .8; this.selection.visible = false; this.scene.add(this.selection);
      this.host.dataset.ready = 'true';
      this.setInteraction(this.interaction, this.motionEnabled);
      this.resize(); this.home();
      return this;
    } catch (error) { this.dispose(); throw error; }
  }

  setInteraction(mode, enabled) {
    this.interaction = mode; this.motionEnabled = enabled;
    if (this.controls) this.controls.enableRotate = mode === 'camera';
    if (!enabled || mode !== 'body') this.drag = null;
  }

  observeMotion(motion) {
    if (!this.body || !motion) return;
    if (this.motionFrame && this.bodyOrientation.toArray().every((value, i) => value === motion.orientation_xyzw[i])) return;
    if (!this.motionFrame) {
      this.motionFrame = true;
      const origin = new THREE.Vector3().fromArray(motion.origin_view_m);
      this.body.position.copy(origin);
      for (const child of this.body.children) child.position.sub(origin);
      this.viewFrame.fromArray(motion.body_to_view_xyzw);
      this.contactPoints = null;
    }
    this.bodyOrientation.fromArray(motion.orientation_xyzw);
    this.body.quaternion.copy(this.viewFrame).multiply(this.bodyOrientation).multiply(this.viewFrame.clone().invert());
    this.body.updateMatrixWorld(true);
    this.bounds.setFromObject(this.model);
    this.center.copy(this.body.position);
    if (this.selectedRef) this.select(this.selectedRef);
    this.invalidate();
  }

  pick(event) {
    if (!this.model) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, 1 - (event.clientY - rect.top) / rect.height * 2);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    let node = this.raycaster.intersectObject(this.model, true)[0]?.object;
    while (node && !node.userData.ref) node = node.parent;
    return node;
  }

  select(ref) {
    this.selectedRef = ref;
    const part = this.parts.get(ref);
    if (!this.selection) return;
    this.selection.visible = !!part;
    if (part) this.selection.box.setFromObject(part);
    this.invalidate();
  }

  locate(ref) {
    const part = this.parts.get(ref);
    if (!part || !this.controls) return;
    this.select(ref);
    const box = new THREE.Box3().setFromObject(part), center = box.getCenter(new THREE.Vector3());
    const size = Math.max(.008, box.getSize(new THREE.Vector3()).length());
    const offset = this.camera.position.clone().sub(this.controls.target).normalize();
    const fov = Math.min(THREE.MathUtils.degToRad(this.camera.fov),
      2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2) * this.camera.aspect));
    this.camera.position.copy(center).addScaledVector(offset, Math.min(.3, size / Math.tan(fov / 2)));
    this.controls.target.copy(center); this.controls.update(); this.invalidate();
  }

  home(direction = 'perspective') {
    if (!this.center) return;
    this.camera.up.set(0, 1, 0);
    const vertical = THREE.MathUtils.degToRad(this.camera.fov);
    const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * this.camera.aspect);
    const axis = direction === 'top' ? new THREE.Vector3(0, 1, .0001)
      : direction === 'bottom' ? new THREE.Vector3(0, -1, -.0001) : new THREE.Vector3(-.4, .85, .75);
    if (direction !== 'perspective' && this.body) axis.applyQuaternion(this.body.quaternion);
    if (direction === 'bottom') this.camera.up.set(0, 0, 1);
    axis.normalize();
    this.camera.position.copy(this.center).add(axis); this.camera.lookAt(this.center);
    this.camera.updateMatrixWorld();
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 1);
    let distance = 0;
    for (const x of [this.bounds.min.x, this.bounds.max.x]) for (const y of [this.bounds.min.y, this.bounds.max.y]) for (const z of [this.bounds.min.z, this.bounds.max.z]) {
      const point = new THREE.Vector3(x, y, z).sub(this.center);
      distance = Math.max(distance, point.dot(axis) + Math.max(Math.abs(point.dot(right)) / Math.tan(horizontal / 2), Math.abs(point.dot(up)) / Math.tan(vertical / 2)));
    }
    this.camera.position.copy(this.center).addScaledVector(axis, distance * 1.15);
    this.controls.target.copy(this.center); this.controls.update(); this.invalidate();
  }

  zoom(factor) {
    if (!this.controls) return;
    const offset = this.camera.position.clone().sub(this.controls.target);
    offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, this.controls.minDistance, this.controls.maxDistance));
    this.camera.position.copy(this.controls.target).add(offset); this.controls.update(); this.invalidate();
  }

  updateLED(rgb) {
    if (this.rgb === rgb) return;
    this.rgb = rgb;
    // WS2812 channel bytes represent PWM intensity, not sRGB-encoded pixels.
    // Use linear channel values once; Three performs the display conversion.
    const color = new THREE.Color().setRGB(((rgb || 0) >> 16 & 255) / 255,
      ((rgb || 0) >> 8 & 255) / 255, ((rgb || 0) & 255) / 255, THREE.LinearSRGBColorSpace);
    const strength = Math.max(color.r, color.g, color.b);
    for (const material of this.ledMaterials || []) {
      material.color.set(rgb === null ? 0xb7b797 : 0x151713);
      material.emissive.copy(color); material.emissiveIntensity = 1;
      material.toneMapped = false;
    }
    if (this.ledLight) {
      const hue = color.clone().multiplyScalar(strength ? 1 / strength : 0);
      this.ledLight.color.copy(hue); this.ledLight.intensity = strength * .000025;
      this.ledHalo.material.color.copy(hue); this.ledHalo.material.opacity = strength * .65;
      this.ledHalo.visible = strength > 0;
    }
    this.host.setAttribute('aria-label', rgb === null ? '三维板卡，LED 无数据' : `三维板卡，LED #${rgb.toString(16).padStart(6, '0').toUpperCase()}`);
    this.invalidate();
  }

  // Project actual CAD bounds for the existing USER button overlay.
  project(ref) {
    const node = this.parts.get(ref);
    if (!node || !this.renderer) return null;
    // Bounds in the unrotated body frame keep the contact point on the top of
    // the switch as the board turns. Cache geometry, not sensor state.
    if (!this.contactPoints) {
      const orientation = this.body.quaternion.clone(); this.body.quaternion.identity(); this.body.updateMatrixWorld(true);
      this.contactPoints = new Map();
      for (const [ref, part] of this.parts) {
        const box = new THREE.Box3().setFromObject(part), point = box.getCenter(new THREE.Vector3());
        point.y = box.max.y; this.contactPoints.set(ref, this.body.worldToLocal(point));
      }
      this.body.quaternion.copy(orientation); this.body.updateMatrixWorld(true);
    }
    const point = this.body.localToWorld(this.contactPoints.get(ref).clone());
    const normal = new THREE.Vector3(0, 1, 0).applyQuaternion(this.body.quaternion);
    if (this.camera.position.clone().sub(point).dot(normal) <= 0) return null;
    point.project(this.camera);
    if (Math.abs(point.x) > 1 || Math.abs(point.y) > 1 || point.z < -1 || point.z > 1) return null;
    return {x: (point.x + 1) * this.host.clientWidth / 2, y: (1 - point.y) * this.host.clientHeight / 2};
  }

  resize() {
    const width = this.host.clientWidth, height = this.host.clientHeight;
    if (!this.renderer || !width || !height || this.disposed) return;
    this.renderer.setSize(width, height, false); this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix(); this.invalidate();
  }

  setVisible(visible) { this.visible = visible; if (visible) this.resize(); }

  invalidate() {
    if (this.pending || this.disposed || !this.visible || document.hidden || !this.host.clientWidth) return;
    this.pending = requestAnimationFrame(() => {
      this.pending = null;
      if (!this.model || !this.visible || !this.host.clientWidth) return;
      this.renderer.render(this.scene, this.camera); this.onFrame();
    });
  }

  dispose() {
    this.disposed = true; cancelAnimationFrame(this.pending); this.abort.abort(); this.observer.disconnect();
    this.controls?.dispose();
    const geometries = new Set(), materials = new Set();
    this.scene?.traverse(node => {
      if (node.geometry) geometries.add(node.geometry);
      if (node.material) for (const material of [].concat(node.material)) materials.add(material);
    });
    geometries.forEach(item => item.dispose()); materials.forEach(item => item.dispose());
    this.ledHaloTexture?.dispose();
    this.environment?.dispose(); this.renderer?.dispose(); this.renderer?.domElement.remove();
  }
}
