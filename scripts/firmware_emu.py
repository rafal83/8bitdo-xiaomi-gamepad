"""Isolated ARM/Thumb function emulator. Does not emulate radio, USB, or hardware."""
from pathlib import Path
import sys,struct
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'local/python'))
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_MEM_READ
from unicorn.arm_const import *
BASE=0x08003400
STOP=0x09000000
ARGS=[UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3]
SAVED=[UC_ARM_REG_R4,UC_ARM_REG_R5,UC_ARM_REG_R6,UC_ARM_REG_R7,UC_ARM_REG_R8,UC_ARM_REG_R9,UC_ARM_REG_R10,UC_ARM_REG_R11]

class FirmwareEmu:
    def __init__(self,image,initialize=True):
        self.image=bytes(image)
        self.uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
        self.uc.mem_map(0x08000000,0x20000)
        self.uc.mem_write(BASE,self.image)
        self.uc.mem_map(0x20000000,0x20000)
        self.uc.mem_map(STOP,0x1000)
        self.init_reads=[]
        if initialize:
            def track(uc,access,address,size,value,data):
                if 0x0801b800<=address<0x08020000:self.init_reads.append((address,size))
            h=self.uc.hook_add(UC_HOOK_MEM_READ,track)
            self.call(0x080039f6,0x0801b800,0x20000000,0x580)
            self.call(0x0800c60c,0x0801ba40,0x20000580,0x3a48)
            self.uc.hook_del(h)
    def call(self,address,*args,count=200000):
        for i,r in enumerate(SAVED):self.uc.reg_write(r,0x11110000+i*0x111)
        for r,v in zip(ARGS,args):self.uc.reg_write(r,v)
        self.uc.reg_write(UC_ARM_REG_SP,0x2001f000)
        self.uc.reg_write(UC_ARM_REG_LR,STOP|1)
        self.uc.emu_start(address|1,STOP,count=count)
        if self.uc.reg_read(UC_ARM_REG_PC)!=STOP:raise RuntimeError('Function did not return within instruction budget')
        self.saved_ok=all(self.uc.reg_read(r)==0x11110000+i*0x111 for i,r in enumerate(SAVED))
        self.stack_ok=self.uc.reg_read(UC_ARM_REG_SP)==0x2001f000
        return self.uc.reg_read(UC_ARM_REG_R0)
    def name(self,name):
        self.uc.mem_write(0x20010000,name+b'\0')
        self.uc.mem_write(0x20010400,b'\x01\x02\x03\x04\x05\x06')
        self.uc.mem_write(0x20001224,b'\xaa\xbb')
        result=self.call(0x080040f4,0x20010000,0x20010400)
        subtype=struct.unpack('<H',self.uc.mem_read(0x20001224,2))[0]
        return result,subtype

