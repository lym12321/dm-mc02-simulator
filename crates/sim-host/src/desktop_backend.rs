//! Private process owned by the Simulator desktop; not a public CLI command.
mod cli_support;
mod lab_launch;

use clap::Parser;
use cli_support::{error_code, Signals};
use lab_launch::{admission, merged, LaunchOptions};
use sim_host::{
    lab::{
        project::{LabProject, ProjectFile},
        session::SessionOptions,
    },
    launch::LaunchError,
};
use std::{path::PathBuf, sync::Arc, time::Duration};

#[derive(Parser)]
#[command(
    name = "sim-lab-backend",
    version,
    about = "Private backend for the DM-MC02 Simulator desktop."
)]
struct Options {
    #[command(flatten)]
    launch: LaunchOptions,
    /// Restart when the selected local ELF changes.
    #[arg(long, conflicts_with = "no_watch")]
    watch: bool,
    /// Disable ELF watching saved in the project.
    #[arg(long)]
    no_watch: bool,
    /// Loopback HTTP port; zero chooses an available port.
    #[arg(long, default_value = "0")]
    port: u16,
    /// Directory containing the desktop frontend assets.
    #[arg(long)]
    assets_dir: PathBuf,
}

impl Options {
    fn project(&self) -> Result<(LabProject, Option<Arc<ProjectFile>>), LaunchError> {
        let (mut project, file) = merged(&self.launch, false, false)?;
        if self.watch {
            project.watch_elf = true;
        }
        if self.no_watch {
            project.watch_elf = false;
        }
        project.validate().map_err(admission)?;
        Ok((project, file))
    }
}

fn run(args: Options) -> Result<(), LaunchError> {
    let (project, file) = args.project()?;
    let signals = Signals::new().map_err(admission)?;
    sim_host::lab::web::run_desktop(
        SessionOptions {
            firmware: project.firmware.clone(),
            qemu: args.launch.qemu,
            timeout: Duration::from_secs_f64(project.timeout_secs),
            startup_timeout: Duration::from_secs_f64(project.startup_timeout_secs),
            qmp_path: args.launch.qmp,
            gdb_port: project.gdb_port,
            usbip: project.usbip,
            project: file,
            launch: Some(project),
        },
        args.port,
        &args.assets_dir,
        signals.cancelled.clone(),
    )
    .map_err(|error| LaunchError {
        code: error_code(&error.primary),
        message: std::iter::once(error.to_string())
            .chain(error.notes)
            .collect::<Vec<_>>()
            .join("\n"),
    })
}

fn main() {
    if let Err(error) = run(Options::parse()) {
        eprintln!("error: {}", error.message);
        std::process::exit(error.code);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requires_external_assets_and_accepts_ephemeral_port() {
        assert!(Options::try_parse_from(["sim-lab-backend"]).is_err());
        let args =
            Options::parse_from(["sim-lab-backend", "--assets-dir", "frontend", "--port", "0"]);
        assert_eq!(args.port, 0);
        assert_eq!(args.assets_dir, PathBuf::from("frontend"));
    }

    #[test]
    fn watch_flags_override_project_settings() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sim.lab.json");
        let project = LabProject {
            firmware: Some("board.elf".into()),
            watch_elf: true,
            ..Default::default()
        };
        ProjectFile::open(&path).unwrap().save(&project).unwrap();
        for (extra, expected) in [(None, true), (Some("--no-watch"), false)] {
            let mut argv = vec![
                "sim-lab-backend",
                "--assets-dir",
                "frontend",
                "--project",
                path.to_str().unwrap(),
            ];
            argv.extend(extra);
            assert_eq!(
                Options::parse_from(argv).project().unwrap().0.watch_elf,
                expected
            );
        }
        assert!(
            Options::parse_from([
                "sim-lab-backend",
                "--assets-dir",
                "frontend",
                "--watch",
                "--elf",
                "board.elf"
            ])
            .project()
            .unwrap()
            .0
            .watch_elf
        );
        assert!(Options::try_parse_from([
            "sim-lab-backend",
            "--assets-dir",
            "frontend",
            "--watch",
            "--no-watch"
        ])
        .is_err());
    }
}
