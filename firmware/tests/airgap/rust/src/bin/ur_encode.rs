//! Prints the one-part UR the firmware shows for a CBOR payload:
//! `ur_encode <type> <cbor hex>`

use ambire_airgap_tests::ambire_ur::encode_single;
use ambire_airgap_tests::TestHasher;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let cbor = hex::decode(&args[2]).expect("CBOR hex");
    let mut out = vec![0u8; 64 + 2 * cbor.len() + 8];
    let n = encode_single::<TestHasher>(&args[1], &cbor, &mut out).expect("encodable");
    println!("{}", String::from_utf8(out[..n].to_vec()).unwrap());
}
