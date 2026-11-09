//! Ambire boards, [ambire_camera]: Uniform Resources (BC-UR), the QR transport
//! of the airgapped signing flow, without a heap.
//!
//! What the camera reads is hostile: every length, index and count below is
//! checked before it is used, and every failure is an [`Error`], never a
//! panic. Follows @ngraveio/bc-ur 1.1.13 (what the Ambire app uses):
//!
//! - A UR is `ur:<type>/<body>` (one part) or `ur:<type>/<seq>-<len>/<body>`
//!   (one part of an animated sequence), in any letter case.
//! - The body is "minimal" bytewords (two letters per byte) with a CRC32 of
//!   its bytes at the end.
//! - A multi-part body is the CBOR array `[seqNum, seqLen, messageLen,
//!   checksum, fragment]`. Parts up to `seqLen` carry one fragment each; later
//!   ones XOR several, chosen by a Xoshiro256** generator seeded from the
//!   part's number and the message checksum (the fountain code).
//!
//! The decoder is stricter than the reference: anything malformed, and any
//! part that disagrees with the first one, ends the stream with an error
//! rather than being skipped. Memory: the caller hands in one work buffer,
//! which holds the message and the mixed parts waiting to be reduced. Mixed
//! parts that find no free slot are dropped; the stream repeats, so that only
//! slows the transfer.

/// Largest message (CBOR) accepted. The Ambire app warns above 14 KiB.
pub const MAX_MESSAGE: usize = 16384;
/// Most parts a message may be cut into: the fragment sets are 128-bit masks.
pub const MAX_SEQ_LEN: u32 = 128;
/// Longest UR type ("eth-sign-request" is 16)
pub const MAX_TYPE: usize = 32;
/// The work buffer's message area: the message and its last fragment's padding
pub const MESSAGE_AREA: usize = MAX_MESSAGE + 1024;
/// Fewer pool bytes than this is a firmware bug, not a property of the stream
pub const MIN_POOL: usize = 4096;

/// SHA-256 and CRC32 (ISO-HDLC, as zlib), supplied by the caller: the firmware
/// uses trezor-crypto's, the host tests their own.
pub trait Hasher {
    fn sha256(data: &[u8]) -> [u8; 32];
    fn crc32(data: &[u8]) -> u32;
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Error {
    /// Not a UR (no "ur:" scheme, or not `type/body` / `type/seq/body`)
    NotUr,
    /// The type has letters other than a-z, 0-9 and '-', or is too long
    BadType,
    /// The "seq-len" component is malformed or zero
    BadSequence,
    /// A letter pair that is no byteword, an odd count, or too short a body
    BadBytewords,
    /// The body's own CRC32 does not match
    BadCrc,
    /// The multi-part body is not the expected CBOR array, or disagrees with
    /// its "seq-len" component, or its lengths do not add up
    BadPart,
    /// A part's type, length, checksum or fragment size differs from the
    /// first part's, or one-part and multi-part URs were mixed
    Mismatch,
    /// The message is over MAX_MESSAGE, or cut into more than MAX_SEQ_LEN parts
    TooLarge,
    /// The assembled message fails its checksum, or its padding is not zero
    BadMessage,
    /// The fountain sampler left its range (only a crafted seed does that)
    Sampler,
    /// The work buffer is smaller than MESSAGE_AREA + MIN_POOL (a bug)
    NoRoom,
}

impl Error {
    /// What the screen says
    pub fn message(self) -> &'static str {
        match self {
            Error::NotUr => "The code is not an Ambire request.",
            Error::BadType => "The request's type is malformed.",
            Error::BadSequence => "The code's part number is malformed.",
            Error::BadBytewords => "The code's data is malformed.",
            Error::BadCrc => "The code's checksum is wrong.",
            Error::BadPart => "The code's part data is malformed.",
            Error::Mismatch => "The codes belong to different requests. Scan again.",
            Error::TooLarge => "The request is too large for this device.",
            Error::BadMessage => "The request failed its checksum. Scan again.",
            Error::Sampler => "The code's part data is malformed.",
            Error::NoRoom => "Not enough memory to scan.",
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Status {
    /// The part was used (or was a duplicate); more are needed
    Pending,
    /// The whole message is in the work buffer
    Complete,
}

// --- Bytewords (minimal) ---------------------------------------------------

/// The first and last letters of the 256 bytewords, in byte order
const MINIMAL: &[u8; 512] = b"aeadaoaxaaahamatayasbkbdbnbtbabsbebybgbwbbbzcmchcscfcycwcecackctcxclcpcndkdadsdidedtdrdndwdpdmdldyeheyeoeeecenemetesftfrfnfsfmfhfzfpfwfxfyfefgflfdgagegrgsgtglgwgdgygmgughgohfhghdhkhthphhhlhyhehnhsidiaieihiyioisinimjejzjnjtjljojsjpjkjykpkoktkskkknkgkekikblblalylflslrlplnltloldlelulklgmnmymhmemomumwmdmtmsmknlnyndnsntnnnenboyoeotoxonolospdptpkpypspmplpepfpaprqdqzrerprlrorhrdrkrfryrnrsrtsesasrssskswstspsosgsbsfsntotktitttdtetytltbtstptatnuyuoutueurvtvyvovlvevwvavdvswlwdwmwpwewywswtwnwzwfwkykynylyaytzszoztzczezm";

/// Letter pair (first * 26 + last) -> byte, and which pairs are bytewords
const fn build_lookup() -> ([u8; 676], [u32; 22]) {
    let mut value = [0u8; 676];
    let mut valid = [0u32; 22];
    let mut i = 0;
    while i < 256 {
        let a = (MINIMAL[2 * i] - b'a') as usize;
        let b = (MINIMAL[2 * i + 1] - b'a') as usize;
        let k = a * 26 + b;
        value[k] = i as u8;
        valid[k / 32] |= 1 << (k % 32);
        i += 1;
    }
    (value, valid)
}

const LOOKUP: ([u8; 676], [u32; 22]) = build_lookup();

fn lower(c: u8) -> u8 {
    c.to_ascii_lowercase()
}

fn letter_index(c: u8) -> Option<usize> {
    let c = lower(c);
    if c.is_ascii_lowercase() {
        Some((c - b'a') as usize)
    } else {
        None
    }
}

/// Decodes minimal bytewords in place (byte i lands where letters 2i, 2i+1
/// were) and checks the CRC32 at the end. Returns the body's length, without
/// the CRC.
fn decode_bytewords<H: Hasher>(text: &mut [u8]) -> Result<usize, Error> {
    // At least one byte and the four of the CRC
    if text.len() % 2 != 0 || text.len() < 2 * 5 {
        return Err(Error::BadBytewords);
    }
    let n = text.len() / 2;
    for i in 0..n {
        let a = letter_index(text[2 * i]).ok_or(Error::BadBytewords)?;
        let b = letter_index(text[2 * i + 1]).ok_or(Error::BadBytewords)?;
        let k = a * 26 + b;
        if LOOKUP.1[k / 32] & (1 << (k % 32)) == 0 {
            return Err(Error::BadBytewords);
        }
        text[i] = LOOKUP.0[k];
    }
    let body = n - 4;
    let crc = u32::from_be_bytes([text[body], text[body + 1], text[body + 2], text[body + 3]]);
    if H::crc32(&text[..body]) != crc {
        return Err(Error::BadCrc);
    }
    Ok(body)
}

/// Writes `UR:<TYPE>/<BYTEWORDS>` (upper case: a QR code's alphanumeric mode)
/// for a one-part UR of `cbor` into `out`; returns its length.
pub fn encode_single<H: Hasher>(ur_type: &str, cbor: &[u8], out: &mut [u8]) -> Result<usize, Error> {
    let ty = ur_type.as_bytes();
    if ty.is_empty() || ty.len() > MAX_TYPE || !ty.iter().all(|&c| is_type_char(lower(c))) {
        return Err(Error::BadType);
    }
    if cbor.is_empty() || cbor.len() > MAX_MESSAGE {
        return Err(Error::TooLarge);
    }
    let len = 3 + ty.len() + 1 + 2 * (cbor.len() + 4);
    if out.len() < len {
        return Err(Error::NoRoom);
    }
    out[..3].copy_from_slice(b"UR:");
    let mut at = 3;
    for &c in ty {
        out[at] = c.to_ascii_uppercase();
        at += 1;
    }
    out[at] = b'/';
    at += 1;
    let crc = H::crc32(cbor).to_be_bytes();
    for &byte in cbor.iter().chain(crc.iter()) {
        out[at] = MINIMAL[2 * byte as usize].to_ascii_uppercase();
        out[at + 1] = MINIMAL[2 * byte as usize + 1].to_ascii_uppercase();
        at += 2;
    }
    Ok(at)
}

fn is_type_char(c: u8) -> bool {
    c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-'
}

// --- The multi-part body's CBOR ---------------------------------------------

/// Reads one CBOR head of `major` type: its argument, in its shortest form
/// only (as every encoder of these parts writes it), at most 32 bits.
fn cbor_head(data: &[u8], at: &mut usize, major: u8) -> Result<u32, Error> {
    let first = *data.get(*at).ok_or(Error::BadPart)?;
    *at += 1;
    if first >> 5 != major {
        return Err(Error::BadPart);
    }
    let info = first & 0x1f;
    let size = match info {
        0..=23 => return Ok(u32::from(info)),
        24 => 1,
        25 => 2,
        26 => 4,
        _ => return Err(Error::BadPart),
    };
    let bytes = data.get(*at..*at + size).ok_or(Error::BadPart)?;
    *at += size;
    let mut v = 0u32;
    for &b in bytes {
        v = (v << 8) | u32::from(b);
    }
    // Shortest form: a longer head than needed is not canonical
    let min = match size {
        1 => 24,
        2 => 0x100,
        _ => 0x1_0000,
    };
    if v < min {
        return Err(Error::BadPart);
    }
    Ok(v)
}

struct Part {
    seq_num: u32,
    seq_len: u32,
    msg_len: u32,
    checksum: u32,
    /// Where the fragment starts in the body, and its length
    frag_at: usize,
    frag_len: usize,
}

fn parse_part(body: &[u8]) -> Result<Part, Error> {
    let mut at = 0;
    if cbor_head(body, &mut at, 4)? != 5 {
        return Err(Error::BadPart);
    }
    let seq_num = cbor_head(body, &mut at, 0)?;
    let seq_len = cbor_head(body, &mut at, 0)?;
    let msg_len = cbor_head(body, &mut at, 0)?;
    let checksum = cbor_head(body, &mut at, 0)?;
    let frag_len = cbor_head(body, &mut at, 2)? as usize;
    // The fragment fills the rest of the body exactly
    if frag_len == 0 || body.len() - at != frag_len {
        return Err(Error::BadPart);
    }
    Ok(Part {
        seq_num,
        seq_len,
        msg_len,
        checksum,
        frag_at: at,
        frag_len,
    })
}

// --- The fountain code's fragment chooser -----------------------------------

struct Xoshiro256 {
    s: [u64; 4],
}

impl Xoshiro256 {
    fn new(seed: &[u8; 32]) -> Self {
        let mut s = [0u64; 4];
        for (i, word) in s.iter_mut().enumerate() {
            let mut b = [0u8; 8];
            b.copy_from_slice(&seed[8 * i..8 * i + 8]);
            *word = u64::from_be_bytes(b);
        }
        Self { s }
    }

    fn next(&mut self) -> u64 {
        let s = &mut self.s;
        let result = s[1].wrapping_mul(5).rotate_left(7).wrapping_mul(9);
        let t = s[1] << 17;
        s[2] ^= s[0];
        s[3] ^= s[1];
        s[1] ^= s[2];
        s[0] ^= s[3];
        s[2] ^= t;
        s[3] = s[3].rotate_left(45);
        result
    }

    /// In [0, 1]: 1.0 only for the top 2^10 values, which round up. As the
    /// C++ and Swift references compute it; the JavaScript one divides in
    /// decimal (20 places) first, which can differ in the last bit, too rarely
    /// (~1e-16 a draw) to move a floor() or a comparison.
    fn next_double(&mut self) -> f64 {
        self.next() as f64 / 18_446_744_073_709_551_616.0
    }

    /// floor(next_double() * n): in 0..n, or an error for the values that
    /// round up to 1.0 (the reference then indexes past its arrays)
    fn next_below(&mut self, n: usize) -> Result<usize, Error> {
        // Truncation is floor for the non-negative products here
        let v = (self.next_double() * n as f64) as usize;
        if v < n {
            Ok(v)
        } else {
            Err(Error::Sampler)
        }
    }
}

/// The degree of a mixed part: Vose's alias method over 1/1, 1/2, ... 1/n, as
/// @keystonehq/alias-sampling builds and draws it (same float operations, in
/// the same order)
fn choose_degree(n: usize, rng: &mut Xoshiro256) -> Result<usize, Error> {
    let mut scaled = [0f64; MAX_SEQ_LEN as usize];
    let mut prob = [1f64; MAX_SEQ_LEN as usize];
    let mut alias = [0u8; MAX_SEQ_LEN as usize];
    let mut small = [0u8; MAX_SEQ_LEN as usize];
    let mut large = [0u8; MAX_SEQ_LEN as usize];
    let (mut n_small, mut n_large) = (0, 0);

    let mut sum = 0f64;
    for i in 0..n {
        sum += 1.0 / (i + 1) as f64;
    }
    for i in 0..n {
        scaled[i] = (1.0 / (i + 1) as f64 * n as f64) / sum;
    }
    for i in (0..n).rev() {
        if scaled[i] < 1.0 {
            small[n_small] = i as u8;
            n_small += 1;
        } else {
            large[n_large] = i as u8;
            n_large += 1;
        }
    }
    while n_small > 0 && n_large > 0 {
        n_small -= 1;
        n_large -= 1;
        let less = small[n_small] as usize;
        let more = large[n_large] as usize;
        prob[less] = scaled[less];
        alias[less] = more as u8;
        scaled[more] = (scaled[more] + scaled[less]) - 1.0;
        if scaled[more] < 1.0 {
            small[n_small] = more as u8;
            n_small += 1;
        } else {
            large[n_large] = more as u8;
            n_large += 1;
        }
    }
    // What is left keeps probability 1 (`prob` starts at 1)

    let column = rng.next_below(n)?;
    let draw = rng.next_double();
    if draw >= 1.0 {
        // The reference then picks an alias that may not exist
        return Err(Error::Sampler);
    }
    let pick = if draw < prob[column] {
        column
    } else {
        alias[column] as usize
    };
    Ok(pick + 1)
}

/// The fragments part `seq_num` carries, as a mask over fragment indexes
/// (public for the host tests, which compare it with the reference)
pub fn choose_fragments<H: Hasher>(seq_num: u32, seq_len: u32, checksum: u32) -> Result<u128, Error> {
    if seq_num <= seq_len {
        return Ok(1u128 << (seq_num - 1));
    }
    let mut seed = [0u8; 8];
    seed[..4].copy_from_slice(&seq_num.to_be_bytes());
    seed[4..].copy_from_slice(&checksum.to_be_bytes());
    let mut rng = Xoshiro256::new(&H::sha256(&seed));
    let n = seq_len as usize;
    let degree = choose_degree(n, &mut rng)?;

    // The first `degree` of a shuffle of 0..n; the rest of the shuffle does
    // not change them
    let mut remaining = [0u8; MAX_SEQ_LEN as usize];
    for (i, r) in remaining.iter_mut().enumerate().take(n) {
        *r = i as u8;
    }
    let mut left = n;
    let mut mask = 0u128;
    for _ in 0..degree {
        let at = rng.next_below(left)?;
        mask |= 1u128 << remaining[at];
        remaining.copy_within(at + 1..left, at);
        left -= 1;
    }
    Ok(mask)
}

// --- The assembler -----------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Empty,
    Single,
    Multi,
}

fn xor_into(dst: &mut [u8], src: &[u8]) {
    for (d, s) in dst.iter_mut().zip(src.iter()) {
        *d ^= *s;
    }
}

fn single_index(mask: u128) -> Option<usize> {
    if mask.count_ones() == 1 {
        Some(mask.trailing_zeros() as usize)
    } else {
        None
    }
}

/// Collects the parts of one UR. Holds no buffer itself: every call gets the
/// work buffer (`MESSAGE_AREA` for the message, the rest a pool of mixed
/// parts, each slot a 16-byte index mask and one fragment).
pub struct Assembler {
    ty: [u8; MAX_TYPE],
    ty_len: usize,
    kind: Kind,
    seq_len: u32,
    msg_len: u32,
    checksum: u32,
    frag_len: usize,
    /// Fragments in the message area
    received: u128,
    slots: usize,
    complete: bool,
    /// Parts taken (duplicates included), and mixed parts that found no slot
    pub parts: u32,
    pub dropped: u32,
}

impl Assembler {
    pub const fn new() -> Self {
        Self {
            ty: [0; MAX_TYPE],
            ty_len: 0,
            kind: Kind::Empty,
            seq_len: 0,
            msg_len: 0,
            checksum: 0,
            frag_len: 0,
            received: 0,
            slots: 0,
            complete: false,
            parts: 0,
            dropped: 0,
        }
    }

    pub fn is_complete(&self) -> bool {
        self.complete
    }

    /// The UR type, lower case (empty before the first part)
    pub fn ur_type(&self) -> &[u8] {
        &self.ty[..self.ty_len]
    }

    /// The assembled message (CBOR), once complete
    pub fn message<'a>(&self, work: &'a [u8]) -> Option<&'a [u8]> {
        if self.complete {
            work.get(..self.msg_len as usize)
        } else {
            None
        }
    }

    /// Fragments in hand and fragments in all (1 and 1 for a one-part UR,
    /// 0 and 0 before the first part)
    pub fn progress(&self) -> (u32, u32) {
        match self.kind {
            Kind::Empty => (0, 0),
            Kind::Single => (1, 1),
            Kind::Multi => (self.received.count_ones(), self.seq_len),
        }
    }

    /// Takes one QR code's text. `text` is used up (decoded in place).
    pub fn receive<H: Hasher>(&mut self, work: &mut [u8], text: &mut [u8]) -> Result<Status, Error> {
        if work.len() < MESSAGE_AREA + MIN_POOL {
            return Err(Error::NoRoom);
        }

        // "ur:" <type> "/" [<seq> "/"] <body>
        if text.len() < 3 || !text[..3].eq_ignore_ascii_case(b"ur:") {
            return Err(Error::NotUr);
        }
        let rest = &text[3..];
        let slash1 = rest.iter().position(|&c| c == b'/').ok_or(Error::NotUr)?;
        let ty_len = slash1;
        if ty_len == 0 || ty_len > MAX_TYPE || !rest[..ty_len].iter().all(|&c| is_type_char(lower(c)))
        {
            return Err(Error::BadType);
        }
        let mut ty = [0u8; MAX_TYPE];
        for (t, &c) in ty.iter_mut().zip(rest[..ty_len].iter()) {
            *t = lower(c);
        }
        let after_type = 3 + slash1 + 1;
        let tail = &text[after_type..];
        let (seq, body_at) = match tail.iter().position(|&c| c == b'/') {
            None => (None, after_type),
            Some(slash2) => {
                if tail[slash2 + 1..].contains(&b'/') {
                    return Err(Error::NotUr);
                }
                (Some(parse_seq(&tail[..slash2])?), after_type + slash2 + 1)
            }
        };

        // The type must match the first part's
        if self.kind != Kind::Empty && self.ur_type() != &ty[..ty_len] {
            return Err(Error::Mismatch);
        }

        let body_len = decode_bytewords::<H>(&mut text[body_at..])?;
        let body = &text[body_at..body_at + body_len];

        match seq {
            None => self.receive_single(work, &ty, ty_len, body),
            Some((seq_num, seq_len)) => {
                self.receive_part::<H>(work, &ty, ty_len, seq_num, seq_len, body)
            }
        }
    }

    fn set_type(&mut self, ty: &[u8; MAX_TYPE], ty_len: usize) {
        self.ty = *ty;
        self.ty_len = ty_len;
    }

    fn receive_single(
        &mut self,
        work: &mut [u8],
        ty: &[u8; MAX_TYPE],
        ty_len: usize,
        body: &[u8],
    ) -> Result<Status, Error> {
        match self.kind {
            Kind::Multi => return Err(Error::Mismatch),
            Kind::Single => {
                // Done already; the same code seen again
                self.parts += 1;
                return Ok(Status::Complete);
            }
            Kind::Empty => {}
        }
        if body.len() > MAX_MESSAGE {
            return Err(Error::TooLarge);
        }
        work[..body.len()].copy_from_slice(body);
        self.set_type(ty, ty_len);
        self.kind = Kind::Single;
        self.msg_len = body.len() as u32;
        self.complete = true;
        self.parts = 1;
        Ok(Status::Complete)
    }

    fn receive_part<H: Hasher>(
        &mut self,
        work: &mut [u8],
        ty: &[u8; MAX_TYPE],
        ty_len: usize,
        seq_num: u32,
        seq_len: u32,
        body: &[u8],
    ) -> Result<Status, Error> {
        let part = parse_part(body)?;
        if part.seq_num != seq_num || part.seq_len != seq_len {
            return Err(Error::BadPart);
        }
        if part.msg_len == 0 {
            return Err(Error::BadPart);
        }
        if part.seq_len > MAX_SEQ_LEN || part.msg_len as usize > MAX_MESSAGE {
            return Err(Error::TooLarge);
        }
        // seq_len = ceil(msg_len / frag_len): the encoder's partition
        let frag_len = part.frag_len;
        let padded = part.seq_len as usize * frag_len;
        if padded < part.msg_len as usize
            || padded - frag_len >= part.msg_len as usize
            || padded > MESSAGE_AREA
        {
            return Err(Error::BadPart);
        }

        match self.kind {
            Kind::Single => return Err(Error::Mismatch),
            Kind::Multi => {
                if part.seq_len != self.seq_len
                    || part.msg_len != self.msg_len
                    || part.checksum != self.checksum
                    || frag_len != self.frag_len
                {
                    return Err(Error::Mismatch);
                }
            }
            Kind::Empty => {
                self.set_type(ty, ty_len);
                self.kind = Kind::Multi;
                self.seq_len = part.seq_len;
                self.msg_len = part.msg_len;
                self.checksum = part.checksum;
                self.frag_len = frag_len;
                self.received = 0;
                self.slots = (work.len() - MESSAGE_AREA) / (16 + frag_len);
                // Every slot free (mask 0); the message area's padding zero
                for slot in 0..self.slots {
                    let at = self.slot_at(slot);
                    work[at..at + 16].fill(0);
                }
                work[..MESSAGE_AREA].fill(0);
            }
        }
        self.parts += 1;
        if self.complete {
            return Ok(Status::Complete);
        }

        let mask = choose_fragments::<H>(part.seq_num, part.seq_len, part.checksum)?;
        let fragment = &body[part.frag_at..part.frag_at + frag_len];
        self.take(work, mask, fragment)?;

        let all = if self.seq_len == 128 {
            u128::MAX
        } else {
            (1u128 << self.seq_len) - 1
        };
        if self.received != all {
            return Ok(Status::Pending);
        }
        let msg_len = self.msg_len as usize;
        if work[msg_len..padded].iter().any(|&b| b != 0) || H::crc32(&work[..msg_len]) != self.checksum
        {
            return Err(Error::BadMessage);
        }
        self.complete = true;
        Ok(Status::Complete)
    }

    fn slot_at(&self, slot: usize) -> usize {
        MESSAGE_AREA + slot * (16 + self.frag_len)
    }

    fn slot_mask(&self, work: &[u8], slot: usize) -> u128 {
        let at = self.slot_at(slot);
        let mut b = [0u8; 16];
        b.copy_from_slice(&work[at..at + 16]);
        u128::from_le_bytes(b)
    }

    fn set_slot_mask(&self, work: &mut [u8], slot: usize, mask: u128) {
        let at = self.slot_at(slot);
        work[at..at + 16].copy_from_slice(&mask.to_le_bytes());
    }

    fn fragment_range(&self, index: usize) -> core::ops::Range<usize> {
        index * self.frag_len..(index + 1) * self.frag_len
    }

    fn slot_data_range(&self, slot: usize) -> core::ops::Range<usize> {
        let at = self.slot_at(slot) + 16;
        at..at + self.frag_len
    }

    /// XORs fragment `index` (in the message area) into slot `slot`
    fn xor_fragment_into_slot(&self, work: &mut [u8], index: usize, slot: usize) {
        let src = self.fragment_range(index);
        let dst = self.slot_data_range(slot);
        let (head, tail) = work.split_at_mut(dst.start);
        xor_into(&mut tail[..self.frag_len], &head[src]);
    }

    /// Takes a part carrying the fragments in `mask`, XORed together
    fn take(&mut self, work: &mut [u8], mut mask: u128, fragment: &[u8]) -> Result<(), Error> {
        if let Some(index) = single_index(mask) {
            if self.received & (1u128 << index) == 0 {
                work[self.fragment_range(index)].copy_from_slice(fragment);
                self.resolved(work, index);
            }
            return Ok(());
        }

        // A mixed part already in the pool
        for slot in 0..self.slots {
            if self.slot_mask(work, slot) == mask {
                return Ok(());
            }
        }

        // Needs a slot to be reduced in: a free one, or it is dropped
        let Some(slot) = (0..self.slots).find(|&s| self.slot_mask(work, s) == 0) else {
            self.dropped += 1;
            return Ok(());
        };
        work[self.slot_data_range(slot)].copy_from_slice(fragment);

        // Reduce by the fragments in hand
        let known = mask & self.received;
        for index in 0..self.seq_len as usize {
            if known & (1u128 << index) != 0 {
                self.xor_fragment_into_slot(work, index, slot);
            }
        }
        mask &= !known;

        // Reduce by the mixed parts whose fragments are all in this one
        for other in 0..self.slots {
            let m = self.slot_mask(work, other);
            if other != slot && m != 0 && m & mask == m && m != mask {
                let src = self.slot_data_range(other);
                let dst = self.slot_data_range(slot);
                xor_ranges(work, dst, src);
                mask &= !m;
            }
        }

        match mask.count_ones() {
            // Nothing new in it
            0 => {}
            1 => {
                let index = mask.trailing_zeros() as usize;
                let src = self.slot_data_range(slot);
                work.copy_within(src, index * self.frag_len);
                self.resolved(work, index);
            }
            _ => {
                // Reduce the pool's supersets of it, then keep it
                for other in 0..self.slots {
                    let m = self.slot_mask(work, other);
                    if other != slot && m & mask == mask && m != mask {
                        let src = self.slot_data_range(slot);
                        let dst = self.slot_data_range(other);
                        xor_ranges(work, dst, src);
                        self.set_slot_mask(work, other, m & !mask);
                    }
                }
                self.set_slot_mask(work, slot, mask);
                // Supersets that came down to one fragment
                self.settle(work);
            }
        }
        Ok(())
    }

    /// Fragment `index` is now in the message area: reduce the pool by it,
    /// and follow every mixed part that comes down to one fragment
    fn resolved(&mut self, work: &mut [u8], index: usize) {
        self.received |= 1u128 << index;
        for slot in 0..self.slots {
            let m = self.slot_mask(work, slot);
            if m & (1u128 << index) != 0 {
                self.xor_fragment_into_slot(work, index, slot);
                self.set_slot_mask(work, slot, m & !(1u128 << index));
            }
        }
        self.settle(work);
    }

    /// Moves every pool part that carries one fragment (or none still
    /// missing) out of the pool, until none is left
    fn settle(&mut self, work: &mut [u8]) {
        loop {
            let mut moved = false;
            for slot in 0..self.slots {
                let m = self.slot_mask(work, slot);
                if m == 0 {
                    continue;
                }
                let missing = m & !self.received;
                if missing != m {
                    // Holds fragments in hand: reduce them out first
                    for index in 0..self.seq_len as usize {
                        if (m & !missing) & (1u128 << index) != 0 {
                            self.xor_fragment_into_slot(work, index, slot);
                        }
                    }
                    self.set_slot_mask(work, slot, missing);
                }
                match single_index(missing) {
                    Some(index) => {
                        self.set_slot_mask(work, slot, 0);
                        let src = self.slot_data_range(slot);
                        work.copy_within(src, index * self.frag_len);
                        self.received |= 1u128 << index;
                        moved = true;
                    }
                    None if missing == 0 => self.set_slot_mask(work, slot, 0),
                    None => {}
                }
            }
            if !moved {
                return;
            }
        }
    }
}

/// `work[dst] ^= work[src]` for two ranges of equal length that do not overlap
fn xor_ranges(work: &mut [u8], dst: core::ops::Range<usize>, src: core::ops::Range<usize>) {
    if dst.start < src.start {
        let (a, b) = work.split_at_mut(src.start);
        xor_into(&mut a[dst], &b[..src.end - src.start]);
    } else {
        let (a, b) = work.split_at_mut(dst.start);
        xor_into(&mut b[..dst.end - dst.start], &a[src]);
    }
}

/// "<seq>-<len>": decimal, no sign or leading zero, both at least 1
fn parse_seq(s: &[u8]) -> Result<(u32, u32), Error> {
    let dash = s.iter().position(|&c| c == b'-').ok_or(Error::BadSequence)?;
    let num = parse_decimal(&s[..dash])?;
    let len = parse_decimal(&s[dash + 1..])?;
    if num == 0 || len == 0 {
        return Err(Error::BadSequence);
    }
    Ok((num, len))
}

fn parse_decimal(s: &[u8]) -> Result<u32, Error> {
    if s.is_empty() || s.len() > 10 || (s[0] == b'0' && s.len() > 1) {
        return Err(Error::BadSequence);
    }
    let mut v: u64 = 0;
    for &c in s {
        if !c.is_ascii_digit() {
            return Err(Error::BadSequence);
        }
        v = v * 10 + u64::from(c - b'0');
    }
    u32::try_from(v).map_err(|_| Error::BadSequence)
}
