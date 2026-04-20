use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .setup(|app| {
      // Log plugin for debug builds
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      // Get main window and configure
      if let Some(window) = app.get_webview_window("main") {
        // Set window title
        let _ = window.set_title("VIP Agent");

        // macOS: show window after content loads (avoids white flash)
        #[cfg(target_os = "macos")]
        {
          let w = window.clone();
          std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(500));
            let _ = w.show();
          });
        }
      }

      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
