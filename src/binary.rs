//! OSB1: sorted compact little-endian states; bit zero is classical bit zero.
use crate::error::{AppError, bad};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
pub const MAX_ENTRIES: usize = 65536;
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BitString(pub [u64; 4]);
impl Ord for BitString {
    fn cmp(&self, other: &Self) -> Ordering {
        self.0.iter().rev().cmp(other.0.iter().rev())
    }
}
impl PartialOrd for BitString {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl BitString {
    pub fn bytes(self) -> Vec<u8> {
        self.0.iter().flat_map(|w| w.to_le_bytes()).collect()
    }
    pub fn from_bytes(bytes: &[u8]) -> Self {
        let mut full = [0u8; 32];
        full[..bytes.len()].copy_from_slice(bytes);
        Self(std::array::from_fn(|i| {
            u64::from_le_bytes(full[i * 8..i * 8 + 8].try_into().unwrap())
        }))
    }
    pub fn parse(s: &str, width: u16) -> Result<Self, AppError> {
        if !(1..=256).contains(&width)
            || s.len() != width as usize
            || !s.bytes().all(|b| b == b'0' || b == b'1')
        {
            return Err(bad("state must be a fixed-width binary string"));
        }
        let mut words = [0u64; 4];
        for (i, b) in s.bytes().rev().enumerate() {
            if b == b'1' {
                words[i / 64] |= 1u64 << (i % 64);
            }
        }
        Ok(Self(words))
    }
    pub fn string(self, width: u16) -> String {
        (0..width as usize)
            .rev()
            .map(|i| {
                if self.0[i / 64] >> (i % 64) & 1 == 1 {
                    '1'
                } else {
                    '0'
                }
            })
            .collect()
    }
}
#[derive(Clone, Copy, Debug)]
pub enum Value {
    Count(u32),
    Weight(f64),
}
#[derive(Debug)]
pub struct Block {
    pub width: u16,
    pub weighted: bool,
    pub entries: Vec<(BitString, Value)>,
    pub shots: Option<i64>,
    pub weight_sum: Option<f64>,
}
pub fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn decode(bytes: &[u8]) -> Result<Block, AppError> {
    if bytes.len() < 16 || &bytes[..4] != b"OSB1" || bytes[7] != 0 || bytes[6] > 1 {
        return Err(bad("invalid OSB1 header"));
    }
    let width = u16::from_le_bytes(bytes[4..6].try_into().unwrap());
    if !(1..=256).contains(&width) {
        return Err(bad("width must be 1..256"));
    }
    let weighted = bytes[6] == 1;
    let n = u64::from_le_bytes(bytes[8..16].try_into().unwrap());
    if n == 0 || n > MAX_ENTRIES as u64 {
        return Err(bad("block must contain 1..65536 entries"));
    }
    let state_len = (width as usize).div_ceil(8);
    let stride = state_len + if weighted { 8 } else { 4 };
    if bytes.len() != 16 + n as usize * stride {
        return Err(bad("incorrect OSB1 length"));
    }
    let mut entries = Vec::with_capacity(n as usize);
    let mut shots = 0i64;
    let mut weight_sum = 0f64;
    for record in bytes[16..].chunks_exact(stride) {
        if width % 8 != 0 && record[state_len - 1] >> (width % 8) != 0 {
            return Err(bad("nonzero state padding"));
        }
        let state = BitString::from_bytes(&record[..state_len]);
        if entries.last().is_some_and(|(prev, _)| *prev >= state) {
            return Err(bad("states must be unique and sorted numerically"));
        }
        let value = if weighted {
            let v = f64::from_le_bytes(record[state_len..].try_into().unwrap());
            if !v.is_finite() {
                return Err(bad("weights must be finite"));
            }
            weight_sum += v;
            Value::Weight(v)
        } else {
            let v = u32::from_le_bytes(record[state_len..].try_into().unwrap());
            if v == 0 {
                return Err(bad("counts must be positive"));
            }
            shots += v as i64;
            Value::Count(v)
        };
        entries.push((state, value));
    }
    if !weight_sum.is_finite() {
        return Err(bad("weight sum overflow"));
    }
    Ok(Block {
        width,
        weighted,
        entries,
        shots: (!weighted).then_some(shots),
        weight_sum: weighted.then_some(weight_sum),
    })
}
pub fn encode(width: u16, entries: &[(BitString, Value)]) -> Vec<u8> {
    let weighted = entries
        .first()
        .is_some_and(|entry| matches!(entry.1, Value::Weight(_)));
    let mut bytes = b"OSB1".to_vec();
    bytes.extend(width.to_le_bytes());
    bytes.extend([u8::from(weighted), 0]);
    bytes.extend((entries.len() as u64).to_le_bytes());
    for (state, value) in entries {
        bytes.extend(&state.bytes()[..(width as usize).div_ceil(8)]);
        match value {
            Value::Count(v) => bytes.extend(v.to_le_bytes()),
            Value::Weight(v) => bytes.extend(v.to_le_bytes()),
        }
    }
    bytes
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn wide_order_and_padding() {
        let a = BitString::parse(&format!("1{}", "0".repeat(255)), 256).unwrap();
        let b = BitString([u64::MAX; 4]);
        assert!(a < b);
        let data = encode(256, &[(a, Value::Count(u32::MAX)), (b, Value::Count(1))]);
        let block = decode(&data).unwrap();
        assert_eq!(block.shots, Some(u32::MAX as i64 + 1));
        assert_eq!(block.entries[0].0, a);
        let mut bad_data = encode(3, &[(BitString([1, 0, 0, 0]), Value::Count(1))]);
        bad_data[16] = 0x81;
        assert!(decode(&bad_data).is_err());
    }
    #[test]
    fn reject_duplicates_zero_nan() {
        let a = BitString([0; 4]);
        assert!(decode(&encode(1, &[(a, Value::Count(0))])).is_err());
        assert!(decode(&encode(1, &[(a, Value::Weight(f64::NAN))])).is_err());
        assert!(decode(&encode(1, &[(a, Value::Count(1)), (a, Value::Count(1))])).is_err());
    }
}
