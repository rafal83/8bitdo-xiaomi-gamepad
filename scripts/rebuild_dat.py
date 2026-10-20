"""Reconstruct the validated single-section DAT container, without flashing.

Byte-exact reconstruction validates the file codec, NOT bootloader acceptance,
on-device integrity checks, hardware recovery, or controller compatibility.
"""
from pathlib import Path
import sys,hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'third_party/8cryptdo'))
import cryptdo8 as c

ORIGINAL_DAT = ROOT / 'local/original.dat'
ORIGINAL_DAT_SHA = '0dc85f34bb0beba631076683c8ef3acc3e0d5babcfe62c5333b1af9acaee3798'
ORIGINAL_BIN_SHA = '01fd743be33e49aeead30fe974a5da680644fb477e4daf92e6f23b173539e43c'


def load_original():
    """Read the user's own official DAT; no original firmware is distributed."""
    if not ORIGINAL_DAT.is_file():
        raise FileNotFoundError('Firmware officiel absent : ajouter local/original.dat (voir README.md).')
    dat = ORIGINAL_DAT.read_bytes()
    if hashlib.sha256(dat).hexdigest() != ORIGINAL_DAT_SHA:
        raise ValueError('Firmware officiel incompatible : seul le DAT 2.07 documente est accepte.')
    sections = c.parse_image(dat)
    if len(sections) != 1:
        raise ValueError('Le firmware officiel doit contenir une seule section.')
    original = c.decrypt_section(dat, sections[0], c.CIPHERS['gd32'])
    if len(original) != 100352 or hashlib.sha256(original).hexdigest() != ORIGINAL_BIN_SHA:
        raise ValueError('Le contenu dechiffre ne correspond pas a la base attendue.')
    return original
def rebuild(dat,original,modified):
    if hashlib.sha256(dat).hexdigest()!='0dc85f34bb0beba631076683c8ef3acc3e0d5babcfe62c5333b1af9acaee3798':
        raise ValueError('Unknown DAT; this script is specific to the supplied image')
    secs=c.parse_image(dat)
    if len(secs)!=1:raise ValueError('Expected one section')
    s=secs[0];cipher=c.CIPHERS['gd32']
    if c.decrypt_section(dat,s,cipher)!=original:raise ValueError('Original BIN is not the DAT plaintext')
    def encode(payload):return c.build_section(payload,cipher,s['id'],addr=s['addr'],length=s['payload_len'],pid=s['pid'])
    if encode(original)!=dat:raise ValueError('Original DAT roundtrip failed')
    if len(modified) not in (len(original), len(original)+1024):
        raise ValueError('Only the stock size or the bounded 1 KiB feedback extension is accepted')
    result=c.build_section(modified,cipher,s['id'],addr=s['addr'],length=len(modified),pid=s['pid'])
    if result[:8]!=dat[:8] or result[12:28]!=dat[12:28] or len(result)!=len(modified)+28:
        raise ValueError('Unexpected header/length change')
    if c.decrypt_section(result,c.parse_image(result)[0],cipher)!=modified:raise ValueError('Modified DAT roundtrip failed')
    return result
