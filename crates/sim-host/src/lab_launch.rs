//! Shared project and launch options for terminal tools and the desktop owner.
use crate::cli_support::seconds;
use clap::{Args, ValueEnum};
use sim_host::{
    lab::{
        ImuMode, StartupMode,
        project::{LabProject, ProjectFile},
    },
    launch::LaunchError,
};
use std::{path::PathBuf, sync::Arc, time::Duration};

#[derive(Clone, Copy, ValueEnum)]
pub(crate) enum Startup {
    Run,
    WaitGdb,
    User,
}
impl From<Startup> for StartupMode {
    fn from(value: Startup) -> Self {
        match value {
            Startup::Run => Self::Run,
            Startup::WaitGdb => Self::WaitGdb,
            Startup::User => Self::User,
        }
    }
}
#[derive(Clone, Copy, ValueEnum)]
pub(crate) enum Imu {
    Off,
    Stationary,
    BoardPose,
}
impl From<Imu> for ImuMode {
    fn from(value: Imu) -> Self {
        match value {
            Imu::Off => Self::Off,
            Imu::Stationary => Self::Stationary,
            Imu::BoardPose => Self::BoardPose,
        }
    }
}
#[derive(Args, Default)]
pub(crate) struct LaunchOptions {
    #[arg(long)]
    pub(crate) project: Option<PathBuf>,
    #[arg(long)]
    pub(crate) elf: Option<PathBuf>,
    #[arg(long, value_enum)]
    pub(crate) startup: Option<Startup>,
    #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
    pub(crate) user_hold_ms: Option<u64>,
    #[arg(long = "uart", value_parser = ["uart1", "uart2", "uart3", "uart5", "uart7", "uart10"])]
    pub(crate) uarts: Vec<String>,
    #[arg(long = "can", value_parser = ["can1", "can2", "can3"])]
    pub(crate) cans: Vec<String>,
    #[arg(long, num_args=0..=1, default_missing_value="true", value_parser=clap::value_parser!(bool))]
    pub(crate) adc: Option<bool>,
    /// IMU input: board pose, fixed stationary sample, or off.
    #[arg(long, value_enum)]
    pub(crate) imu: Option<Imu>,
    #[arg(long)]
    pub(crate) qemu: Option<PathBuf>,
    /// Active host deadline including startup; cleanup has separate budgets.
    #[arg(long, value_parser = seconds)]
    pub(crate) timeout: Option<Duration>,
    #[arg(long, value_parser = seconds)]
    pub(crate) startup_timeout: Option<Duration>,
    #[arg(long)]
    pub(crate) qmp: Option<PathBuf>,
    #[arg(long, value_parser = clap::value_parser!(u16).range(1..), conflicts_with="no_gdb")]
    pub(crate) gdb_port: Option<u16>,
    #[arg(long)]
    pub(crate) no_gdb: bool,
    /// Export the guest USB device over loopback USB/IP (default 127.0.0.1:3240).
    #[arg(long, num_args=0..=1, default_missing_value="127.0.0.1:3240", conflicts_with="no_usbip")]
    pub(crate) usbip: Option<std::net::SocketAddr>,
    #[arg(long)]
    pub(crate) no_usbip: bool,
}
pub(crate) fn admission(error: impl std::fmt::Display) -> LaunchError {
    LaunchError::admission(error.to_string())
}
pub(crate) fn merged(
    args: &LaunchOptions,
    legacy_interactive: bool,
    paused: bool,
) -> Result<(LabProject, Option<Arc<ProjectFile>>), LaunchError> {
    let file = args
        .project
        .as_ref()
        .map(ProjectFile::open)
        .transpose()
        .map_err(admission)?
        .map(Arc::new);
    let mut project = match &file {
        Some(file) => file.resolve(file.project()).map_err(admission)?,
        None => LabProject::default(),
    };
    if legacy_interactive && file.is_none() {
        project.uarts = vec!["uart1".into()];
        project.cans.clear();
        project.adc = false;
        project.timeout_secs = 300.0;
    }
    if let Some(elf) = &args.elf {
        project.firmware = Some(if elf.is_absolute() {
            elf.clone()
        } else {
            std::env::current_dir().map_err(admission)?.join(elf)
        });
    }
    if let Some(startup) = args.startup {
        project.startup = startup.into();
    }
    if paused {
        project.startup = StartupMode::WaitGdb;
    }
    if let Some(hold) = args.user_hold_ms {
        project.user_hold_ms = hold;
    }
    if !args.uarts.is_empty() {
        project.uarts = args.uarts.clone();
    }
    if !args.cans.is_empty() {
        project.cans = args.cans.clone();
    }
    if let Some(adc) = args.adc {
        project.adc = adc;
    }
    if let Some(imu) = args.imu {
        project.imu = imu.into();
    }
    if let Some(timeout) = args.timeout {
        project.timeout_secs = timeout.as_secs_f64();
    }
    if let Some(timeout) = args.startup_timeout {
        project.startup_timeout_secs = timeout.as_secs_f64();
    }
    if args.gdb_port.is_some() {
        project.gdb_port = args.gdb_port;
    }
    if args.no_gdb {
        project.gdb_port = None;
    }
    if args.usbip.is_some() {
        project.usbip = args.usbip;
    }
    if args.no_usbip {
        project.usbip = None;
    }
    project.validate().map_err(admission)?;
    Ok((project, file))
}
