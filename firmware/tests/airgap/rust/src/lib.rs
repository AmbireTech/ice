//! The firmware's UR codec, built for the host with test hashers.

#[path = "../../../../trezor-firmware/core/embed/rust/src/ui/layout_delizia/component/ambire_ur.rs"]
pub mod ambire_ur;

pub struct TestHasher;

impl ambire_ur::Hasher for TestHasher {
    fn sha256(data: &[u8]) -> [u8; 32] {
        use sha2::Digest;
        sha2::Sha256::digest(data).into()
    }

    fn crc32(data: &[u8]) -> u32 {
        crc32fast::hash(data)
    }
}
