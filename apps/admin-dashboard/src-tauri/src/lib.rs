use tauri::Manager;
use tauri::Url;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Navigate main window to Vercel URL
            if let Some(window) = app.get_webview_window("main") {
                let url = Url::parse("https://oasisvip.vercel.app").unwrap();
                let _ = window.navigate(url);
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
