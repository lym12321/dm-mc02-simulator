//! Prescribed rigid-body rotation about a fixed origin, sampled in virtual time.
//! Coordinates are right handed, SI; quaternions are [x, y, z, w].
//! The caller supplies the sensor mounting transform and owns time and transport.

use crate::signals::ImuSample;
use glam::{DQuat, DVec3, DVec4};
use sim_core::{Error, Result};
use std::sync::Arc;

const TRANSITION_S: f64 = 0.1;
const GRAVITY: DVec3 = DVec3::new(0.0, 0.0, -9.80665);

fn refused(message: &str) -> Arc<dyn std::error::Error + Send + Sync> {
    Arc::new(Error::Admission(message.into()))
}

pub fn quaternion(value: [f64; 4]) -> Result<DQuat> {
    let v = DVec4::from_array(value);
    let scale = v.abs().max_element();
    if !v.is_finite() || scale == 0.0 {
        return Err(refused("orientation requires a finite nonzero quaternion"));
    }
    // Scaling first also accepts finite very large/small caller representations.
    Ok(DQuat::from_array((v / scale).normalize().to_array()))
}

#[derive(Clone, Copy, Debug)]
pub struct PoseSample {
    pub orientation_xyzw: [f64; 4],
    pub angular_velocity_world_rad_s: [f64; 3],
    pub angular_acceleration_world_rad_s2: [f64; 3],
    pub imu: ImuSample,
}

pub struct BoardPose {
    sensor_in_body: DQuat,
    sensor_offset_m: DVec3,
    start_ns: u64,
    start: DVec4,
    tangent: DVec4,
    target: DVec4,
}

impl BoardPose {
    /// sensor_in_body rotates sensor-local vectors into body coordinates.
    pub fn new(sensor_in_body_xyzw: [f64; 4], sensor_offset_m: [f64; 3]) -> Result<Self> {
        let sensor_in_body = quaternion(sensor_in_body_xyzw)?;
        let sensor_offset_m = DVec3::from_array(sensor_offset_m);
        if !sensor_offset_m.is_finite() {
            return Err(refused("sensor offset requires finite metres"));
        }
        let identity = DVec4::from_array(DQuat::IDENTITY.to_array());
        Ok(Self {
            sensor_in_body,
            sensor_offset_m,
            start_ns: 0,
            start: identity,
            tangent: DVec4::ZERO,
            target: identity,
        })
    }

    /// Retarget a normalized quaternion Hermite curve, preserving orientation
    /// and angular velocity. Wall time never enters the trajectory.
    pub fn target(&mut self, orientation_xyzw: [f64; 4], timestamp_ns: u64) -> Result<()> {
        let mut target = DVec4::from_array(quaternion(orientation_xyzw)?.to_array());
        let (start, derivative, _) = self.evaluate(timestamp_ns)?;
        if start.dot(target) < 0.0 {
            target = -target;
        }
        self.start = start;
        self.tangent = derivative * TRANSITION_S;
        self.target = target;
        self.start_ns = timestamp_ns;
        Ok(())
    }

    fn evaluate(&self, timestamp_ns: u64) -> Result<(DVec4, DVec4, DVec4)> {
        let elapsed = timestamp_ns
            .checked_sub(self.start_ns)
            .ok_or_else(|| refused("pose virtual time moved backwards"))?;
        let u = elapsed as f64 * 1e-9 / TRANSITION_S;
        if u >= 1.0 {
            return Ok((self.target, DVec4::ZERO, DVec4::ZERO));
        }
        let u2 = u * u;
        let u3 = u2 * u;
        let p = self.start * (2.0 * u3 - 3.0 * u2 + 1.0)
            + self.tangent * (u3 - 2.0 * u2 + u)
            + self.target * (-2.0 * u3 + 3.0 * u2);
        let dp = (self.start * (6.0 * u2 - 6.0 * u)
            + self.tangent * (3.0 * u2 - 4.0 * u + 1.0)
            + self.target * (-6.0 * u2 + 6.0 * u))
            / TRANSITION_S;
        let ddp = (self.start * (12.0 * u - 6.0)
            + self.tangent * (6.0 * u - 4.0)
            + self.target * (-12.0 * u + 6.0))
            / TRANSITION_S.powi(2);
        let length = p.length();
        if !length.is_finite() || length < 1e-12 {
            return Err(refused("pose trajectory is singular"));
        }
        let q = p / length;
        let speed = q.dot(dp);
        let dq = (dp - q * speed) / length;
        let d_speed = dq.dot(dp) + q.dot(ddp);
        let ddq = (ddp - q * d_speed - dq * (2.0 * speed)) / length;
        Ok((q, dq, ddq))
    }

    pub fn sample(&self, timestamp_ns: u64) -> Result<PoseSample> {
        let (q, dq, ddq) = self.evaluate(timestamp_ns)?;
        let body = DQuat::from_array(q.to_array());
        let omega = DQuat::from_array(dq.to_array()) * body.conjugate();
        let alpha = DQuat::from_array(ddq.to_array()) * body.conjugate();
        let omega = DVec3::new(omega.x, omega.y, omega.z) * 2.0;
        let alpha = DVec3::new(alpha.x, alpha.y, alpha.z) * 2.0;
        let r = body * self.sensor_offset_m;
        let acceleration = alpha.cross(r) + omega.cross(omega.cross(r));
        let world_to_sensor = (body * self.sensor_in_body).conjugate();
        let imu = ImuSample {
            gyro_rad_s: (world_to_sensor * omega).to_array(),
            accel_m_s2: (world_to_sensor * (acceleration - GRAVITY)).to_array(),
        };
        imu.validate()?;
        Ok(PoseSample {
            orientation_xyzw: q.to_array(),
            angular_velocity_world_rad_s: omega.to_array(),
            angular_acceleration_world_rad_s2: alpha.to_array(),
            imu,
        })
    }
}
