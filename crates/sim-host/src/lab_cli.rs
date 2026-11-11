use crate::{
    cli_support::{error_code, Signals},
    lab_launch::{admission, merged, LaunchOptions},
};
use clap::{Args, Subcommand, ValueEnum};
use sim_host::{lab::interactive::run_interactive_with_project, launch::LaunchError};
#[cfg(unix)]
use std::os::fd::AsFd;
#[cfg(windows)]
use std::os::windows::io::AsHandle;
use std::{
    io::{self, Write},
    time::Duration,
};

#[derive(Args)]
pub struct Options {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    /// Observe native UART/CAN and enter public board IO commands.
    Interactive(InteractiveOptions),
    /// Print a native debugger attach configuration.
    Ide(IdeOptions),
}

#[derive(Args)]
struct InteractiveOptions {
    #[command(flatten)]
    launch: LaunchOptions,
    /// Start paused for native GDB (requires an enabled GDB port).
    #[arg(long, conflicts_with = "startup")]
    paused: bool,
}
#[derive(Clone, Copy, ValueEnum)]
enum IdeFormat {
    Vscode,
    Gdb,
    Clion,
}
#[derive(Args)]
struct IdeOptions {
    #[command(flatten)]
    launch: LaunchOptions,
    #[arg(long, value_enum, default_value = "vscode")]
    format: IdeFormat,
}

pub fn run(options: Options) -> Result<i32, LaunchError> {
    match options.command {
        Command::Interactive(args) => interactive(args),
        Command::Ide(args) => {
            let (project, _) = merged(&args.launch, false, false)?;
            let config = project.ide_config().map_err(admission)?;
            let mut out = io::stdout().lock();
            match args.format {
                IdeFormat::Gdb => out
                    .write_all(config["gdb"].as_str().expect("GDB text").as_bytes())
                    .map_err(admission)?,
                IdeFormat::Vscode | IdeFormat::Clion => {
                    let key = match args.format {
                        IdeFormat::Vscode => "vscode",
                        _ => "clion",
                    };
                    serde_json::to_writer_pretty(&mut out, &config[key]).map_err(admission)?;
                    writeln!(out).map_err(admission)?;
                }
            }
            Ok(0)
        }
    }
}
fn interactive(args: InteractiveOptions) -> Result<i32, LaunchError> {
    let (project, _) = merged(&args.launch, true, args.paused)?;
    if project.watch_elf {
        return Err(admission(
            "ELF watching requires the Simulator desktop; set watch_elf to false for interactive",
        ));
    }
    let commands = project.commands().map_err(admission)?;
    let signals = Signals::new().map_err(admission)?;
    let options = project
        .lab_options(
            args.launch.qemu,
            args.launch.qmp,
            Some(signals.cancelled.clone()),
        )
        .map_err(admission)?;
    let input = io::stdin();
    #[cfg(unix)]
    let handle = input.as_fd();
    #[cfg(windows)]
    let handle = input.as_handle();
    let mut output = io::stdout().lock();
    let result = run_interactive_with_project(
        options,
        handle,
        &mut output,
        project.startup,
        Duration::from_millis(project.user_hold_ms),
        &commands,
    )
    .map_err(|error| LaunchError {
        code: error_code(&error.primary),
        message: std::iter::once(error.to_string())
            .chain(error.notes)
            .collect::<Vec<_>>()
            .join("\n"),
    })?;
    writeln!(
        output,
        "Lab finished: {} commands, {} observation batches, end_ns={}",
        result.commands, result.observation_batches, result.end_ns
    )
    .map_err(admission)?;
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;
    use sim_host::lab::{
        project::{LabProject, ProjectFile},
        ImuMode, StartupMode,
    };
    #[derive(Parser)]
    struct Cli {
        #[command(flatten)]
        options: InteractiveOptions,
    }

    #[test]
    fn project_settings_survive_defaults_and_explicit_flags_override() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sim.lab.json");
        let project = LabProject {
            firmware: Some("build/board.elf".into()),
            timeout_secs: 45.0,
            startup_timeout_secs: 2.0,
            gdb_port: Some(5555),
            ..Default::default()
        };
        ProjectFile::open(&path).unwrap().save(&project).unwrap();
        let args = Cli::parse_from(["sim", "--project", path.to_str().unwrap()]).options;
        let (merged, _) = super::merged(&args.launch, true, args.paused).unwrap();
        assert_eq!(
            (
                merged.timeout_secs,
                merged.startup_timeout_secs,
                merged.gdb_port
            ),
            (45.0, 2.0, Some(5555))
        );
        assert_eq!(merged.firmware.unwrap(), dir.path().join("build/board.elf"));
        assert_eq!(merged.imu, ImuMode::Stationary);
        let args = Cli::parse_from([
            "sim",
            "--project",
            path.to_str().unwrap(),
            "--timeout",
            "60.5",
            "--no-gdb",
            "--adc=false",
            "--imu",
            "off",
            "--elf",
            "override.elf",
        ])
        .options;
        let (merged, _) = super::merged(&args.launch, true, args.paused).unwrap();
        assert_eq!(
            (merged.timeout_secs, merged.gdb_port, merged.adc),
            (60.5, None, false)
        );
        assert_eq!(merged.imu, ImuMode::Off);
        assert_eq!(
            merged.firmware.unwrap(),
            std::env::current_dir().unwrap().join("override.elf")
        );
    }
    #[test]
    fn legacy_interactive_defaults_and_project_gdb_paused_are_preserved() {
        let args = Cli::parse_from(["sim", "--elf", "board.elf"]).options;
        let (project, _) = merged(&args.launch, true, false).unwrap();
        assert_eq!(
            (
                project.uarts,
                project.cans,
                project.adc,
                project.timeout_secs
            ),
            (vec!["uart1".to_owned()], vec![], false, 300.0)
        );
        let args = Cli::parse_from([
            "sim",
            "--elf",
            "board.elf",
            "--gdb-port",
            "1234",
            "--paused",
        ])
        .options;
        assert_eq!(
            merged(&args.launch, true, args.paused).unwrap().0.startup,
            StartupMode::WaitGdb
        );
    }
    #[test]
    fn cli_exposes_terminal_tools_only() {
        #[derive(Parser)]
        struct LabCli {
            #[command(flatten)]
            options: Options,
        }
        for removed in ["web", "serve"] {
            assert!(LabCli::try_parse_from(["sim", removed]).is_err());
        }
        assert!(LabCli::try_parse_from(["sim", "interactive"]).is_ok());
        assert!(LabCli::try_parse_from(["sim", "ide"]).is_ok());
    }

    #[test]
    fn interactive_rejects_desktop_file_watching() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sim.lab.json");
        let project = LabProject {
            firmware: Some("board.elf".into()),
            watch_elf: true,
            ..Default::default()
        };
        ProjectFile::open(&path).unwrap().save(&project).unwrap();
        let args = Cli::parse_from(["sim", "--project", path.to_str().unwrap()]).options;
        assert!(interactive(args)
            .unwrap_err()
            .to_string()
            .contains("requires the Simulator desktop"));
    }
}
