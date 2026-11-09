//! The UR assembler against streams the Ambire app's own libraries made
//! (VECTORS=path to the output of ../vectors.js), and against hostile ones.

use ambire_airgap_tests::ambire_ur::{
    choose_fragments, encode_single, Assembler, Error, Status, MAX_MESSAGE, MESSAGE_AREA, MIN_POOL,
};
use ambire_airgap_tests::TestHasher as H;
use serde_json::Value;

/// The firmware's work buffer is what the image buffer has left after the QR
/// decoder; tests use about as much, and a minimal one
const WORK: usize = 40 * 1024;

fn vectors() -> Value {
    let path = std::env::var("VECTORS").expect("VECTORS=<vectors.json from ../vectors.js>");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn requests(v: &Value) -> Vec<&Value> {
    v["requests"].as_array().unwrap().iter().collect()
}

fn parts(r: &Value) -> Vec<Vec<u8>> {
    r["parts"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| p.as_str().unwrap().as_bytes().to_vec())
        .collect()
}

fn cbor(r: &Value) -> Vec<u8> {
    hex::decode(r["cbor"].as_str().unwrap()).unwrap()
}

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        self.0 >> 33
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
    fn shuffle<T>(&mut self, v: &mut [T]) {
        for i in (1..v.len()).rev() {
            let j = self.below(i + 1);
            v.swap(i, j);
        }
    }
}

/// Feeds parts until complete; returns how many were needed, or the error
fn feed(work: &mut [u8], asm: &mut Assembler, parts: &[Vec<u8>]) -> Result<Option<usize>, Error> {
    for (i, p) in parts.iter().enumerate() {
        let mut text = p.clone();
        if asm.receive::<H>(work, &mut text)? == Status::Complete {
            return Ok(Some(i + 1));
        }
    }
    Ok(None)
}

fn assemble(parts: &[Vec<u8>], work_len: usize) -> (Result<Option<usize>, Error>, Assembler, Vec<u8>) {
    let mut work = vec![0xa5u8; work_len];
    let mut asm = Assembler::new();
    let res = feed(&mut work, &mut asm, parts);
    (res, asm, work)
}

fn check_complete(name: &str, r: &Value, parts: &[Vec<u8>], work_len: usize) -> usize {
    let (res, asm, work) = assemble(parts, work_len);
    let used = res.unwrap_or_else(|e| panic!("{name}: {e:?}")).unwrap_or_else(|| {
        panic!("{name}: incomplete after {} parts ({:?})", parts.len(), asm.progress())
    });
    assert_eq!(asm.ur_type(), b"eth-sign-request", "{name}");
    assert_eq!(asm.message(&work).unwrap(), &cbor(r)[..], "{name}: message differs");
    used
}

#[test]
fn fragment_choice_matches_reference() {
    let v = vectors();
    for f in v["fragments"].as_array().unwrap() {
        let seq_num = f["seqNum"].as_u64().unwrap() as u32;
        let seq_len = f["seqLen"].as_u64().unwrap() as u32;
        let checksum = f["checksum"].as_u64().unwrap() as u32;
        let mut expected = 0u128;
        for i in f["indexes"].as_array().unwrap() {
            expected |= 1u128 << i.as_u64().unwrap();
        }
        let got = choose_fragments::<H>(seq_num, seq_len, checksum).unwrap();
        assert_eq!(got, expected, "seqNum {seq_num} seqLen {seq_len} checksum {checksum:#x}");
    }
}

#[test]
fn streams_in_order() {
    let v = vectors();
    for r in requests(&v) {
        let name = r["name"].as_str().unwrap();
        let p = parts(r);
        if name == "oversize" {
            let (res, _, _) = assemble(&p, WORK);
            assert_eq!(res, Err(Error::TooLarge), "{name}");
            continue;
        }
        let used = check_complete(name, r, &p, WORK);
        let seq_len = r["seqLen"].as_u64().unwrap() as usize;
        assert_eq!(used, seq_len, "{name}: the pure parts alone complete it");
    }
}

#[test]
fn streams_lower_case() {
    let v = vectors();
    for r in requests(&v).into_iter().filter(|r| r["name"] != "oversize") {
        let p: Vec<Vec<u8>> = parts(r).into_iter().map(|t| t.to_ascii_lowercase()).collect();
        check_complete(r["name"].as_str().unwrap(), r, &p, WORK);
    }
}

#[test]
fn streams_shuffled_with_duplicates_and_losses() {
    let v = vectors();
    let mut rng = Rng(7);
    for r in requests(&v).into_iter().filter(|r| r["name"] != "oversize") {
        let name = r["name"].as_str().unwrap();
        for round in 0..20 {
            let mut p = parts(r);
            // Lose about a third, see some twice, out of order
            p.retain(|_| rng.below(3) != 0);
            let dups: Vec<Vec<u8>> = p.iter().filter(|_| rng.below(4) == 0).cloned().collect();
            p.extend(dups);
            rng.shuffle(&mut p);
            let (res, asm, work) = assemble(&p, WORK);
            match res {
                Ok(Some(_)) => assert_eq!(asm.message(&work).unwrap(), &cbor(r)[..], "{name}"),
                // Losing parts can leave a fountain short; never wrong
                Ok(None) => assert!(!asm.is_complete(), "{name} round {round}"),
                Err(e) => panic!("{name} round {round}: {e:?}"),
            }
        }
    }
}

#[test]
fn streams_from_mixed_parts_only() {
    let v = vectors();
    for r in requests(&v).into_iter().filter(|r| r["name"] != "oversize") {
        let name = r["name"].as_str().unwrap();
        let seq_len = r["seqLen"].as_u64().unwrap() as usize;
        if seq_len == 1 {
            continue;
        }
        let p = parts(r);
        let mixed = &p[seq_len..];
        let used = check_complete(name, r, mixed, WORK);
        eprintln!("{name}: {seq_len} fragments from {used} mixed parts");
    }
}

#[test]
fn small_pool_still_completes() {
    let v = vectors();
    for r in requests(&v).into_iter().filter(|r| r["name"] != "oversize") {
        let name = r["name"].as_str().unwrap();
        let seq_len = r["seqLen"].as_u64().unwrap() as usize;
        let p = parts(r);
        // Mixed parts first: they fill the small pool, then the pure ones
        let mut order: Vec<Vec<u8>> = p[seq_len..].to_vec();
        order.extend_from_slice(&p[..seq_len]);
        check_complete(name, r, &order, MESSAGE_AREA + MIN_POOL);
    }
}

#[test]
fn work_buffer_too_small_is_an_error() {
    let v = vectors();
    let p = parts(requests(&v)[0]);
    let (res, _, _) = assemble(&p, MESSAGE_AREA + MIN_POOL - 1);
    assert_eq!(res, Err(Error::NoRoom));
}

// --- Encoding ---------------------------------------------------------------

#[test]
fn encode_single_round_trips() {
    let v = vectors();
    for r in requests(&v).into_iter().filter(|r| r["seqLen"] == 1) {
        let c = cbor(r);
        let mut out = vec![0u8; 3000];
        let n = encode_single::<H>("eth-sign-request", &c, &mut out).unwrap();
        // The app's own encoder wrote exactly this
        assert_eq!(&out[..n], r["parts"][0].as_str().unwrap().as_bytes());
        let (res, asm, work) = assemble(&[out[..n].to_vec()], WORK);
        assert_eq!(res, Ok(Some(1)));
        assert_eq!(asm.message(&work).unwrap(), &c[..]);
    }
    let mut small = [0u8; 10];
    assert_eq!(encode_single::<H>("bytes", &[1, 2, 3], &mut small), Err(Error::NoRoom));
    assert_eq!(encode_single::<H>("bad type", &[1], &mut [0u8; 64]), Err(Error::BadType));
    assert_eq!(encode_single::<H>("bytes", &[], &mut [0u8; 64]), Err(Error::TooLarge));
}

// --- Hostile streams ----------------------------------------------------------

fn one(text: &[u8]) -> Result<Status, Error> {
    let mut work = vec![0u8; WORK];
    let mut asm = Assembler::new();
    asm.receive::<H>(&mut work, &mut text.to_vec())
}

/// A UR text from a CBOR body, with an optional "seq-len" component
fn ur_text(ty: &str, seq: Option<&str>, body: &[u8]) -> Vec<u8> {
    let mut out = vec![0u8; 3 * 1024 + 2 * body.len()];
    let n = encode_single::<H>(ty, body, &mut out).unwrap();
    let s = String::from_utf8(out[..n].to_vec()).unwrap();
    match seq {
        None => s.into_bytes(),
        Some(seq) => {
            let (head, words) = s.split_once('/').unwrap();
            format!("{head}/{seq}/{words}").into_bytes()
        }
    }
}

fn cbor_uint(major: u8, v: u64, out: &mut Vec<u8>) {
    let m = major << 5;
    if v < 24 {
        out.push(m | v as u8);
    } else if v < 0x100 {
        out.extend([m | 24, v as u8]);
    } else if v < 0x1_0000 {
        out.push(m | 25);
        out.extend((v as u16).to_be_bytes());
    } else {
        out.push(m | 26);
        out.extend((v as u32).to_be_bytes());
    }
}

fn part_body(seq_num: u64, seq_len: u64, msg_len: u64, checksum: u64, fragment: &[u8]) -> Vec<u8> {
    let mut b = vec![0x85];
    for v in [seq_num, seq_len, msg_len, checksum] {
        cbor_uint(0, v, &mut b);
    }
    cbor_uint(2, fragment.len() as u64, &mut b);
    b.extend_from_slice(fragment);
    b
}

#[test]
fn malformed_texts() {
    let v = vectors();
    let single = parts(requests(&v)[0])[0].clone();
    let s = String::from_utf8(single.clone()).unwrap();

    assert_eq!(one(b""), Err(Error::NotUr));
    assert_eq!(one(b"https://ambire.com"), Err(Error::NotUr));
    assert_eq!(one(b"UR:ETH-SIGN-REQUEST"), Err(Error::NotUr));
    assert_eq!(one(s.replacen("UR:", "UX:", 1).as_bytes()), Err(Error::NotUr));
    assert_eq!(one(s.replacen("ETH-SIGN-REQUEST", "ETH_SIGN", 1).as_bytes()), Err(Error::BadType));
    assert_eq!(one(s.replacen("ETH-SIGN-REQUEST", "", 1).as_bytes()), Err(Error::BadType));
    let long = format!("UR:{}/{}", "A".repeat(33), &s[20..]);
    assert_eq!(one(long.as_bytes()), Err(Error::BadType));
    // Three path components
    assert_eq!(one(s.replacen("/", "/1-1/2-2/", 1).as_bytes()), Err(Error::NotUr));

    // The body
    let mut odd = single.clone();
    odd.pop();
    assert_eq!(one(&odd), Err(Error::BadBytewords));
    let mut short = single.clone();
    short.truncate(short.len() - 2);
    assert_eq!(one(&short), Err(Error::BadCrc));
    assert_eq!(one(b"UR:BYTES/AEADAOAX"), Err(Error::BadBytewords)); // under 5 bytes
    let mut junk = single.clone();
    let at = junk.len() - 12;
    junk[at] = b'Q'; // "q?" starts no byteword... nor does a digit
    let r = one(&junk);
    assert!(matches!(r, Err(Error::BadBytewords) | Err(Error::BadCrc)), "{r:?}");
    junk[at] = b'1';
    assert_eq!(one(&junk), Err(Error::BadBytewords));
    // A valid byteword in place of another: the CRC catches it
    let mut flipped = single.clone();
    let at = flipped.len() - 20;
    let (a, b) = (flipped[at], flipped[at + 1]);
    flipped[at] = if (a, b) == (b'A', b'E') { b'A' } else { b'A' };
    flipped[at + 1] = if (a, b) == (b'A', b'E') { b'D' } else { b'E' };
    assert_eq!(one(&flipped), Err(Error::BadCrc));
}

#[test]
fn malformed_sequences() {
    let body = part_body(1, 2, 300, 0x1234_5678, &[0u8; 150]);
    for seq in ["0-2", "1-0", "01-2", "1-02", "1", "-2", "1-", "a-2", "1-2-3", "+1-2", "1-99999999999"] {
        assert_eq!(one(&ur_text("bytes", Some(seq), &body)), Err(Error::BadSequence), "{seq}");
    }
    // Path and body disagree
    assert_eq!(one(&ur_text("bytes", Some("2-2"), &body)), Err(Error::BadPart));
    assert_eq!(one(&ur_text("bytes", Some("1-3"), &body)), Err(Error::BadPart));
    assert_eq!(one(&ur_text("bytes", Some("1-2"), &body)), Ok(Status::Pending));
}

#[test]
fn malformed_part_bodies() {
    let ok = |b: &[u8], seq: &str| one(&ur_text("bytes", Some(seq), b));
    let frag = [7u8; 150];
    // Lengths that do not add up
    assert_eq!(ok(&part_body(1, 2, 301, 1, &frag), "1-2"), Err(Error::BadPart)); // needs 3
    assert_eq!(ok(&part_body(1, 3, 300, 1, &frag), "1-3"), Err(Error::BadPart)); // 2 suffice
    assert_eq!(ok(&part_body(1, 2, 0, 1, &frag), "1-2"), Err(Error::BadPart));
    assert_eq!(ok(&part_body(1, 2, 300, 1, &[]), "1-2"), Err(Error::BadPart));
    // Too large
    assert_eq!(ok(&part_body(1, 129, 129 * 10, 1, &[0u8; 10]), "1-129"), Err(Error::TooLarge));
    let big = MAX_MESSAGE as u64 + 1;
    assert_eq!(ok(&part_body(1, 2, big, 1, &vec![0u8; (big as usize + 1) / 2]), "1-2"), Err(Error::TooLarge));
    // Not the five-item array
    let mut b = part_body(1, 2, 300, 1, &frag);
    b[0] = 0x84;
    assert_eq!(ok(&b, "1-2"), Err(Error::BadPart));
    // A trailing byte, a missing one
    let mut b = part_body(1, 2, 300, 1, &frag);
    b.push(0);
    assert_eq!(ok(&b, "1-2"), Err(Error::BadPart));
    let mut b = part_body(1, 2, 300, 1, &frag);
    b.pop();
    assert_eq!(ok(&b, "1-2"), Err(Error::BadPart));
    // A non-shortest head (1 written as 0x18 0x01)
    let mut b = vec![0x85, 0x18, 0x01];
    b.extend_from_slice(&part_body(1, 2, 300, 1, &frag)[2..]);
    assert_eq!(ok(&b, "1-2"), Err(Error::BadPart));
    // A 64-bit or indefinite head
    let mut b = vec![0x85, 0x1b, 0, 0, 0, 0, 0, 0, 0, 1];
    b.extend_from_slice(&part_body(1, 2, 300, 1, &frag)[2..]);
    assert_eq!(ok(&b, "1-2"), Err(Error::BadPart));
    let mut b = part_body(1, 2, 300, 1, &frag);
    b[0] = 0x9f;
    assert_eq!(ok(&b, "1-2"), Err(Error::BadPart));
    // A text string for the fragment
    let mut b = part_body(1, 2, 300, 1, &frag);
    let at = b.len() - frag.len() - 2;
    b[at] = 0x78;
    assert_eq!(ok(&b, "1-2"), Err(Error::BadPart));
}

#[test]
fn inconsistent_streams() {
    let v = vectors();
    let reqs = requests(&v);
    let by_name = |n: &str| reqs.iter().find(|r| r["name"] == n).unwrap();
    let a = parts(by_name("safe-tx"));
    let b = parts(by_name("mail-arrays")); // same type and part count, other message

    let mut work = vec![0u8; WORK];
    let mut asm = Assembler::new();
    assert_eq!(asm.receive::<H>(&mut work, &mut a[0].clone()), Ok(Status::Pending));
    assert_eq!(asm.receive::<H>(&mut work, &mut b[1].clone()), Err(Error::Mismatch));

    // One-part after multi-part, and the other way round
    let single = parts(by_name("legacy"))[0].clone();
    let mut asm = Assembler::new();
    asm.receive::<H>(&mut work, &mut a[0].clone()).unwrap();
    assert_eq!(asm.receive::<H>(&mut work, &mut single.clone()), Err(Error::Mismatch));
    let mut asm = Assembler::new();
    asm.receive::<H>(&mut work, &mut single.clone()).unwrap();
    assert_eq!(asm.receive::<H>(&mut work, &mut a[0].clone()), Err(Error::Mismatch));

    // Another type
    let mut asm = Assembler::new();
    asm.receive::<H>(&mut work, &mut a[0].clone()).unwrap();
    let other = String::from_utf8(a[1].clone()).unwrap().replacen("ETH-SIGN-REQUEST", "ETH-SIGNATURE", 1);
    assert_eq!(asm.receive::<H>(&mut work, &mut other.into_bytes()), Err(Error::Mismatch));

    // Same stream, one part re-encoded with another message length (its own
    // CRC valid): mismatch
    let frag = [1u8; 100];
    let mut asm = Assembler::new();
    asm.receive::<H>(&mut work, &mut ur_text("bytes", Some("1-3"), &part_body(1, 3, 300, 9, &frag))).unwrap();
    for (num, len, msg, sum, f) in [
        (2, 3, 299, 9, &frag[..]),
        (2, 3, 300, 8, &frag[..]),
        (2, 4, 400, 9, &frag[..]),
        (2, 3, 297, 9, &frag[..99]),
    ] {
        let mut t = ur_text("bytes", Some(&format!("{num}-{len}")), &part_body(num, len, msg, sum, f));
        assert_eq!(asm.receive::<H>(&mut work, &mut t), Err(Error::Mismatch), "{num}-{len} {msg} {sum}");
    }
}

#[test]
fn corrupt_fragment_fails_the_message_checksum() {
    // Consistent headers, valid part CRCs, one fragment altered
    let msg: Vec<u8> = (0..300u32).map(|i| i as u8).collect();
    let sum = crc32fast::hash(&msg) as u64;
    let mut work = vec![0u8; WORK];
    let mut asm = Assembler::new();
    let mut f1 = msg[..150].to_vec();
    asm.receive::<H>(&mut work, &mut ur_text("bytes", Some("1-2"), &part_body(1, 2, 300, sum, &f1))).unwrap();
    f1[0] ^= 1;
    let f2 = &msg[150..];
    let mut t = ur_text("bytes", Some("2-2"), &part_body(2, 2, 300, sum, f2));
    assert_eq!(asm.receive::<H>(&mut work, &mut t), Ok(Status::Complete));

    let mut asm = Assembler::new();
    asm.receive::<H>(&mut work, &mut ur_text("bytes", Some("1-2"), &part_body(1, 2, 300, sum, &f1))).unwrap();
    let mut t = ur_text("bytes", Some("2-2"), &part_body(2, 2, 300, sum, f2));
    assert_eq!(asm.receive::<H>(&mut work, &mut t), Err(Error::BadMessage));

    // Non-zero padding after the message
    let msg: Vec<u8> = (0..299u32).map(|i| i as u8).collect();
    let sum = crc32fast::hash(&msg) as u64;
    let mut last = msg[150..].to_vec();
    last.push(0xff);
    let mut asm = Assembler::new();
    asm.receive::<H>(&mut work, &mut ur_text("bytes", Some("1-2"), &part_body(1, 2, 299, sum, &msg[..150]))).unwrap();
    let mut t = ur_text("bytes", Some("2-2"), &part_body(2, 2, 299, sum, &last));
    assert_eq!(asm.receive::<H>(&mut work, &mut t), Err(Error::BadMessage));
}

#[test]
fn fuzz_never_panics() {
    let v = vectors();
    let mut rng = Rng(99);
    let alphabet = b"AEDOXHMTYSBKNLCFWRPGUZIJVQaeUR:/-0123456789";
    let streams: Vec<Vec<Vec<u8>>> = requests(&v).iter().map(|r| parts(r)).collect();
    for _ in 0..20000 {
        let mut work = vec![0u8; MESSAGE_AREA + MIN_POOL + rng.below(4096)];
        let mut asm = Assembler::new();
        let s = &streams[rng.below(streams.len())];
        for _ in 0..1 + rng.below(8) {
            let mut t = s[rng.below(s.len())].clone();
            match rng.below(5) {
                // Random letters over part of the text
                0 => {
                    for _ in 0..1 + rng.below(6) {
                        let at = rng.below(t.len());
                        t[at] = alphabet[rng.below(alphabet.len())];
                    }
                }
                // Truncated
                1 => t.truncate(rng.below(t.len() + 1)),
                // A random CBOR body with a valid CRC
                2 => {
                    let n = 1 + rng.below(300);
                    let body: Vec<u8> = (0..n).map(|_| rng.next() as u8).collect();
                    let seq = format!("{}-{}", 1 + rng.below(200), 1 + rng.below(200));
                    t = ur_text("bytes", Some(&seq), &body);
                }
                // A plausible header over a random fragment
                3 => {
                    let seq_len = 1 + rng.below(130) as u64;
                    let frag_len = 1 + rng.below(300);
                    let msg_len = (seq_len - 1) * frag_len as u64 + 1 + rng.below(frag_len) as u64;
                    let num = 1 + rng.below(400) as u64;
                    let frag: Vec<u8> = (0..frag_len).map(|_| rng.next() as u8).collect();
                    let sum = rng.next() & 0xffff_ffff;
                    t = ur_text("bytes", Some(&format!("{num}-{seq_len}")), &part_body(num, seq_len, msg_len, sum, &frag));
                }
                _ => {}
            }
            let _ = asm.receive::<H>(&mut work, &mut t);
        }
    }
}

#[test]
fn self_consistent_random_streams_complete() {
    // Messages of every size class, cut as the app's encoder cuts them
    // (nominal fragment length, mixed parts XORed by the chooser), fed with
    // losses and repeats: each must come out exactly
    let mut rng = Rng(5);
    for _ in 0..300 {
        let len = 1 + rng.below(MAX_MESSAGE);
        let msg: Vec<u8> = (0..len).map(|_| rng.next() as u8).collect();
        let sum = crc32fast::hash(&msg);
        let mut frag_len = 0;
        for count in 1..=len.div_ceil(10) {
            frag_len = len.div_ceil(count);
            if frag_len <= 200 {
                break;
            }
        }
        let seq_len = len.div_ceil(frag_len);
        if seq_len > 128 {
            continue;
        }
        let mut frags = vec![vec![0u8; frag_len]; seq_len];
        for (i, f) in frags.iter_mut().enumerate() {
            let end = ((i + 1) * frag_len).min(len);
            f[..end - i * frag_len].copy_from_slice(&msg[i * frag_len..end]);
        }
        let mut work = vec![0u8; WORK];
        let mut asm = Assembler::new();
        let mut done = false;
        for num in 1..=(seq_len as u32 * 4 + 40) {
            if rng.below(3) == 0 {
                continue;
            }
            let mask = choose_fragments::<H>(num, seq_len as u32, sum).unwrap();
            let mut data = vec![0u8; frag_len];
            for (i, f) in frags.iter().enumerate() {
                if mask & (1u128 << i) != 0 {
                    for (d, s) in data.iter_mut().zip(f) {
                        *d ^= s;
                    }
                }
            }
            let body = part_body(num as u64, seq_len as u64, len as u64, sum as u64, &data);
            let mut t = ur_text("bytes", Some(&format!("{num}-{seq_len}")), &body);
            if asm.receive::<H>(&mut work, &mut t).unwrap() == Status::Complete {
                done = true;
                break;
            }
        }
        if done {
            assert_eq!(asm.message(&work).unwrap(), &msg[..], "len {len}");
        }
    }
}
