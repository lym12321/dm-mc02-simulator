#![cfg(feature = "lab")]

use glam::{DQuat, DVec3};
use sim_host::lab::pose::{BoardPose, quaternion};
use std::f64::consts::{FRAC_PI_2, PI};

fn near(actual: [f64; 3], expected: [f64; 3], tolerance: f64) {
    assert!(
        (DVec3::from_array(actual) - DVec3::from_array(expected)).length() < tolerance,
        "{actual:?} != {expected:?}"
    );
}

#[test]
fn six_static_faces_and_mounting_rotation() {
    let mut pose = BoardPose::new([0., 0., 0., 1.], [0.; 3]).unwrap();
    for (axis, angle, expected) in [
        (DVec3::X, 0., [0., 0., 1.]),
        (DVec3::X, PI, [0., 0., -1.]),
        (DVec3::X, FRAC_PI_2, [0., 1., 0.]),
        (DVec3::X, -FRAC_PI_2, [0., -1., 0.]),
        (DVec3::Y, FRAC_PI_2, [-1., 0., 0.]),
        (DVec3::Y, -FRAC_PI_2, [1., 0., 0.]),
    ] {
        pose.target(
            DQuat::from_axis_angle(axis, angle).to_array(),
            1_000_000_000,
        )
        .unwrap();
        let s = pose.sample(1_100_000_000).unwrap();
        near(s.imu.accel_m_s2, expected.map(|v| v * 9.80665), 1e-10);
        near(s.imu.gyro_rad_s, [0.; 3], 1e-12);
    }
    let mounted = BoardPose::new(DQuat::from_rotation_x(FRAC_PI_2).to_array(), [0.; 3]).unwrap();
    near(
        mounted.sample(0).unwrap().imu.accel_m_s2,
        [0., 9.80665, 0.],
        1e-10,
    );
}

#[test]
fn motion_matches_independent_pose_differences_and_offset_acceleration() {
    let mut pose = BoardPose::new([0., 0., 0., 1.], [0.02, 0., 0.]).unwrap();
    pose.target(DQuat::from_rotation_z(FRAC_PI_2).to_array(), 0)
        .unwrap();
    let t = 37_000_000;
    let dt_ns = 1_000;
    let at = |t| DQuat::from_array(pose.sample(t).unwrap().orientation_xyzw);
    let before = at(t - dt_ns);
    let after = at(t + dt_ns);
    let dt = dt_ns as f64 * 1e-9;
    let omega = (after * before.conjugate()).to_scaled_axis() / (2.0 * dt);
    let sample = pose.sample(t).unwrap();
    near(sample.angular_velocity_world_rad_s, omega.to_array(), 1e-6);
    // Twice differentiate the actual sensor position in the world, independently
    // of the implementation's angular-acceleration/cross-product formula.
    let r = DVec3::new(0.02, 0., 0.);
    let acceleration = (after * r - 2.0 * (at(t) * r) + before * r) / dt.powi(2);
    let specific = at(t).conjugate() * (acceleration + DVec3::Z * 9.80665);
    near(sample.imu.accel_m_s2, specific.to_array(), 2e-5);
}

#[test]
fn retarget_preserves_velocity_and_uses_virtual_time_only() {
    let mut pose = BoardPose::new([0., 0., 0., 1.], [0.; 3]).unwrap();
    pose.target(DQuat::from_rotation_y(1.).to_array(), 0)
        .unwrap();
    let before = pose.sample(40_000_000).unwrap();
    pose.target(DQuat::from_rotation_x(-1.).to_array(), 40_000_000)
        .unwrap();
    let after = pose.sample(40_000_000).unwrap();
    assert!(
        DQuat::from_array(before.orientation_xyzw)
            .angle_between(DQuat::from_array(after.orientation_xyzw))
            .abs()
            < 1e-7
    );
    near(
        before.angular_velocity_world_rad_s,
        after.angular_velocity_world_rad_s,
        1e-10,
    );
    near(
        pose.sample(40_000_000).unwrap().imu.gyro_rad_s,
        after.imu.gyro_rad_s,
        1e-10,
    );
    near(
        pose.sample(140_000_000).unwrap().imu.gyro_rad_s,
        [0.; 3],
        1e-12,
    );
    assert!(pose.sample(39_000_000).is_err());
}

#[test]
fn admission_and_antipodal_quaternions() {
    for value in [[0.; 4], [f64::NAN, 0., 0., 1.], [f64::INFINITY, 0., 0., 1.]] {
        assert!(quaternion(value).is_err());
    }
    for scale in [1e-300, 1e300, -1.] {
        let mut pose = BoardPose::new([0., 0., 0., 1.], [0.; 3]).unwrap();
        pose.target([0., 0., 0., scale], 0).unwrap();
        near(
            pose.sample(50_000_000).unwrap().imu.gyro_rad_s,
            [0.; 3],
            1e-12,
        );
    }
    assert!(BoardPose::new([0., 0., 0., 1.], [f64::NAN, 0., 0.]).is_err());
}
