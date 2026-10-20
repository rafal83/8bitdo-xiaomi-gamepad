"""Build an EXPERIMENTAL v2.07 Xiaomi patch. Offline only; never opens a USB device.

Requires the exact supplied plaintext and Keystone 0.9.2. Every changed byte is
restricted to two verified function prologues and the verified trailing padding.
"""
from pathlib import Path
import hashlib,sys,struct
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'local/python'))
from keystone import Ks,KS_ARCH_ARM,KS_MODE_THUMB,KS_MODE_LITTLE_ENDIAN
import capstone

BASE=0x08003400
LENGTH=100352
SHA='01fd743be33e49aeead30fe974a5da680644fb477e4daf92e6f23b173539e43c'
CAVE=0x0801ba40
NAME=CAVE
REPORT=CAVE+0x90
END=BASE+LENGTH
PROLOGUES={0x080040f4:bytes.fromhex('2de9f047'),0x0800db1c:bytes.fromhex('2de9f84f')}
MD=capstone.Cs(capstone.CS_ARCH_ARM,capstone.CS_MODE_THUMB|capstone.CS_MODE_MCLASS)

def digest(b):return hashlib.sha256(b).hexdigest()

def stock_controller_types(original):
    """Derive the stock dispatch types from the user's validated original image."""
    if len(original) != LENGTH or digest(original) != SHA:
        raise ValueError('Unknown original firmware')
    return sorted({struct.unpack_from('<H', original, start-BASE+i*8+4)[0]
                   for start,count in ((0x0801a435,19),(0x0801a4cd,44))
                   for i in range(count)})
def assemble(source,address):
    # Keystone's comment parser is intentionally kept out of the source path.
    source='\n'.join(line.split('//')[0] for line in source.splitlines())
    encoded,_=Ks(KS_ARCH_ARM,KS_MODE_THUMB|KS_MODE_LITTLE_ENDIAN).asm(source,address)
    if encoded is None:raise ValueError('Assembler produced no output')
    return bytes(encoded)

def build(original):
    if len(original)!=LENGTH or digest(original)!=SHA:raise ValueError('Refusing firmware with unknown length/SHA256')
    for addr,expected in PROLOGUES.items():
        if original[addr-BASE:addr-BASE+4]!=expected:raise ValueError(f'Prologue mismatch at {addr:#x}')
    if original[CAVE-BASE:]!=bytes(END-CAVE):raise ValueError('Code cave is not the verified zero padding')
    # The startup scatter table and zero initializer are part of the safety proof.
    if struct.unpack_from('<IIII',original,0x0801b7f0-BASE)!=(CAVE,0x20000580,0x3a48,0x0800c60c):
        raise ValueError('Startup scatter record differs')
    name=assemble((ROOT/'scripts/firmware/xiaomi_name_hook.s').read_text(),NAME)
    report=assemble((ROOT/'scripts/firmware/xiaomi_report_hook.s').read_text(),REPORT)
    if len(name)>REPORT-NAME or len(report)>END-REPORT:raise ValueError('Injected code exceeds verified padding')
    # Numeric BL/B.W destinations must survive assembler label relaxation exactly.
    name_code=name[:name.index('\u5c0f\u7c73\u84dd\u7259\u624b\u67c4'.encode())]
    report_code=report[:report.index(bytes.fromhex('00024002400040010001800180008002'))]
    def external_branches(code,addr):
        return [(i.mnemonic,int(i.op_str[1:],16)) for i in MD.disasm(code,addr)
                if i.mnemonic in ('bl','b.w')]
    if external_branches(name_code,NAME)!=[('bl',0x08003616),('bl',0x08003616),('b.w',0x080040f8)]:
        raise ValueError('Classifier call/fallback targets differ')
    if external_branches(report_code,REPORT)!=[('b.w',0x0800db20)]:
        raise ValueError('Parser fallback target differs')
    edits=[]
    for addr,target in [(0x080040f4,NAME),(0x0800db1c,REPORT)]:
        if addr%2 or target%2:raise ValueError('Unaligned Thumb branch')
        branch=assemble(f'b.w {target:#x}',addr)
        decoded=list(MD.disasm(branch,addr))
        if len(branch)!=4 or len(decoded)!=1 or decoded[0].mnemonic!='b.w' or int(decoded[0].op_str[1:],16)!=target:
            raise ValueError('Thumb branch did not roundtrip')
        if not -(1<<24)<=target-(addr+4)<(1<<24):raise ValueError('B.W displacement exceeds Thumb range')
        edits.append((addr,branch,f'Entry hook to {target:#010x}'))
    edits += [(NAME,name,'Exact Xiaomi remote-name classifier; stock fallback'),
              (REPORT,report,'Type 0x18 HID report decoder; stock fallback')]
    patched=bytearray(original);journal=[]
    for addr,new,reason in edits:
        off=addr-BASE;old=bytes(patched[off:off+len(new)])
        patched[off:off+len(new)]=new
        journal.append({'address':f'{addr:#010x}','offset':f'{off:#x}','length':len(new),
                        'old':old.hex(),'new':new.hex(),'reason':reason})
    allowed=set()
    for x in journal:allowed.update(range(int(x['offset'],16),int(x['offset'],16)+x['length']))
    actual={i for i,(x,y) in enumerate(zip(original,patched)) if x!=y}
    if not actual<=allowed or patched[:0xec]!=original[:0xec] or len(patched)!=LENGTH:
        raise ValueError('Image integrity invariant failed')
    return bytes(patched),{'status':'experimental_unflashed','original_sha256':SHA,
           'patched_sha256':digest(patched),'length':LENGTH,'changed_byte_count':len(actual),
           'injected_lengths':{'name':len(name),'report':len(report)},'edits':journal}

