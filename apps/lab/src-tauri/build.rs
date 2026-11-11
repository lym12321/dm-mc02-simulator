fn main() {
    let windows = std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows");
    let mut attributes = tauri_build::Attributes::new();
    if windows {
        // Tauri's default resource links its manifest only into the main binary.
        // Use one manifest for every executable, including the library's tests.
        attributes = attributes
            .windows_attributes(tauri_build::WindowsAttributes::new_without_app_manifest());
    }
    tauri_build::try_build(attributes).expect("unable to build Tauri resources");
    if windows {
        println!("cargo:rerun-if-changed=windows/app.manifest");
        println!("cargo:rerun-if-changed=windows/app.rc");
        embed_resource::compile_for_everything("windows/app.rc", embed_resource::NONE)
            .manifest_required()
            .expect("unable to embed the Windows Common Controls manifest");
    }
}
