mod backend;

use std::sync::Arc;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend = Arc::new(backend::Backend::default());
    let owner = backend.clone();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(backend)
        .setup(move |app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || owner.start(handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("unable to create DM-MC02 Simulator window");

    app.run(|handle, event| {
        if matches!(
            event,
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
        ) {
            handle.state::<Arc<backend::Backend>>().shutdown();
        }
    });
}
