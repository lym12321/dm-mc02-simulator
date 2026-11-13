//! Free-running Lab consumers borrow native QEMU time, without Simulation cuts.
pub mod channels;
pub mod command;
mod firmware_image;
#[cfg(feature = "desktop-backend")]
mod ide_export;
pub mod input;
pub mod interactive;
pub mod pose;
#[cfg(feature = "lab-session")]
mod presentation;
pub mod project;
mod runtime;
#[cfg(feature = "lab-session")]
pub mod session;
#[cfg(feature = "desktop-backend")]
#[doc(hidden)]
pub mod web;
pub use runtime::*;
