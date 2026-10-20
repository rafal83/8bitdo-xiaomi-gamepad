# Offline protocol tests: the fake transport never opens USB or Windows HID.
$ErrorActionPreference = 'Stop'
$testSource = @'
public sealed class FakeBootHid : IBootHid {
    public string Scenario;
    public int Sent, Blocks, Received;
    public bool Encoded, Committed, Complete;
    private byte[] last;
    private readonly byte[] dat;
    public FakeBootHid(string scenario, byte[] input) { Scenario = scenario; dat = input; }
    public bool IsPresent { get { return Scenario.IndexOf("gone") < 0; } }
    public void Dispose() { }
    private static void Require(bool value, string message) { if(!value) throw new Exception("MOCK: " + message); }
    private static void Equal(byte[] actual, byte[] expected) {
        Require(actual.Length == expected.Length, "packet size");
        for(int i=0; i<actual.Length; i++) Require(actual[i] == expected[i], "packet byte " + i);
    }
    public void Send(byte[] packet) {
        Require(!Complete && (last == null || Encoded), "send before ACK / after completion");
        Sent++;
        var expected = new byte[64];
        expected[2] = 0x16;
        int payload = 0;
        if(Sent == 1) { expected[0]=5; expected[3]=1; expected[5]=4; }
        else if(Sent == 2) { expected[0]=5; expected[2]=0x1a; expected[3]=1; }
        else if(Sent == 3) {
            expected[5]=1; payload=28;
            Buffer.BlockCopy(dat, 0, expected, 8, 28);
        } else if(Blocks < (dat.Length-28)/32) {
            payload=32;
            Buffer.BlockCopy(dat, 28+32*Blocks, expected, 8, 32);
            Blocks++;
        } else if(!Encoded) {
            payload=12; expected[5]=6;
            // Fixed known UID words XOR protocol keys indexed by each low nibble.
            byte[] encoded={0x17,0x47,0x7b,0xa7,0xd9,0x8d,0xe9,0x4a,0xa5,0x02,0x53,0x2f};
            Buffer.BlockCopy(encoded, 0, expected, 8, 12);
            Encoded=true;
        } else {
            expected[0]=5; expected[3]=1; expected[5]=2; Committed=true;
        }
        if(payload > 0) { expected[0]=(byte)(payload+7); expected[3]=(byte)(payload+3); expected[6]=(byte)payload; }
        Equal(packet, expected);
        last=(byte[])packet.Clone();
    }
    public byte[] Receive() {
        Require(last != null, "read without request");
        Received++;
        var result = new byte[64];
        result[2]=0x16;
        if(Sent == 1) {
            int size=Scenario == "version8" ? 8 : 4;
            result[0]=(byte)(size+8); result[3]=(byte)(size+3); result[5]=4; result[6]=(byte)size;
            result[8]=(byte)(Scenario == "wrong_version" ? 206 : 207);
            if(Scenario == "bad_version") result[6]=3;
        } else if(Sent == 2) {
            result[0]=45; result[2]=0x19; result[3]=36;
            byte[] uid={0x44,0x30,0x20,0x10,0x85,0x77,0x66,0x55,0xe6,0xcd,0xab,0x90};
            if(Scenario != "empty_id") Buffer.BlockCopy(uid,0,result,5,12);
            if(Scenario == "bad_id") result[3]=35;
        } else {
            result[0]=6; result[3]=1; result[5]=0x14;
            if(Scenario == "ack_variant") { result[0]=8; result[3]=3; }
            if((Sent == 3 && Scenario == "header_nak") ||
               (Blocks == 4 && Scenario == "data_nak") ||
               (Committed && Scenario == "final_nak_gone")) result[5]=0x15;
            if(Blocks == 4 && Scenario == "data_timeout") throw new TimeoutException("data timeout");
            if(Committed) {
                if(Scenario == "final_timeout_present" || Scenario == "final_timeout_gone")
                    throw new TimeoutException("final timeout");
                if(Scenario == "final_io_gone") throw new Win32Exception(1167);
                if(Scenario == "final_bad_gone") result[2]=0x19;
                if(Scenario == "final_status_ack" || Scenario == "final_status_bad") {
                    result[0]=9; result[3]=4; result[6]=1; result[8]=(byte)(Scenario == "final_status_ack" ? 1 : 0);
                }
                Complete=true;
            }
        }
        last=null;
        return result;
    }
}

public static class NativeFlasherTests {
    private static void Require(bool value, string message) { if(!value) throw new Exception(message); }
    public static void Run() {
        byte[] dat=new byte[100380];
        uint[] header={207,0x08003400,0x18800,0,0,0,0};
        for(int i=0;i<7;i++) Buffer.BlockCopy(BitConverter.GetBytes(header[i]),0,dat,i*4,4);
        for(int i=28;i<dat.Length;i++) dat[i]=(byte)((i*17+i/127)&255);
        string[] scenarios={"success","extended","version8","ack_variant","wrong_version","bad_version",
            "empty_id","bad_id","header_nak","data_nak","data_timeout","final_nak_gone",
            "final_bad_gone","final_timeout_present","final_timeout_gone","final_io_gone","final_status_ack","final_status_bad"};
        foreach(string scenario in scenarios) {
            byte[] input=dat;
            if(scenario=="extended") {
                input=new byte[101404]; Buffer.BlockCopy(dat,0,input,0,dat.Length);
                Buffer.BlockCopy(BitConverter.GetBytes((uint)101376),0,input,8,4);
                for(int i=dat.Length;i<input.Length;i++) input[i]=(byte)(i*13);
            }
            var hid=new FakeBootHid(scenario,input);
            var progress=new List<int>();
            Exception failure=null;
            try { NativeBootProtocol.Transfer(hid,input,delegate(int p) {
                Require(p != 100 || hid.Committed, "100 before commit");
                Require(progress.Count == 0 || p > progress[progress.Count-1], "progress order");
                progress.Add(p);
            }); } catch(Exception e) { failure=e; }
            if(failure != null && failure.Message.StartsWith("MOCK:")) throw failure;
            bool success=scenario=="success" || scenario=="extended" || scenario=="version8" || scenario=="ack_variant" ||
                         scenario=="final_timeout_gone" || scenario=="final_io_gone" || scenario=="final_status_ack";
            Require((failure==null)==success, "unexpected outcome: "+scenario+" "+failure);
            Require(progress.Contains(100)==success, "wrong completion: "+scenario);
            if(success) {
                int blockCount=(input.Length-28)/32;
                Require(hid.Sent==blockCount+5 && hid.Blocks==blockCount && hid.Encoded && hid.Committed, "incomplete transfer");
                Require(hid.Received==blockCount+4, "unexpected ACK count");
            }
            if(scenario=="wrong_version" || scenario=="empty_id" || scenario=="bad_id")
                Require(hid.Sent==2, "header sent before identification accepted");
            if(scenario=="bad_version") Require(hid.Sent==1,"continued after bad version");
            if(scenario=="header_nak") Require(hid.Sent==3 && hid.Blocks==0,"continued after header NAK");
            if(scenario=="data_nak" || scenario=="data_timeout")
                Require(hid.Blocks==4 && !hid.Encoded && !hid.Committed,"retried or committed after data error");
            Console.WriteLine("PASS "+scenario);
        }
        for(int index=0;index<8;index++) {
            byte[] invalid=index==7 ? new byte[100379] : (byte[])dat.Clone();
            if(index<7) invalid[index*4]^=1;
            var hid=new FakeBootHid("success",dat);
            bool failed=false;
            try { NativeBootProtocol.Transfer(hid,invalid,delegate(int p){}); } catch(InvalidDataException) { failed=true; }
            Require(failed && hid.Sent==0,"invalid DAT touched HID");
        }
        string temp=Path.GetTempFileName();
        try {
            File.WriteAllBytes(temp,dat);
            bool failed=false;
            try { NativeFlasher.Run(temp,new string('0',64)); } catch(InvalidDataException) { failed=true; }
            Require(failed,"bad SHA must fail before USB open");
        } finally { File.Delete(temp); }
        var flags=System.Reflection.BindingFlags.NonPublic;
        string[] names={"Attributes","Caps","Overlapped","InterfaceData"};
        int[] sizes={12,64,IntPtr.Size==8?32:20,IntPtr.Size==8?32:28};
        for(int i=0;i<names.Length;i++)
            Require(Marshal.SizeOf(typeof(WindowsBootHid).GetNestedType(names[i],flags))==sizes[i],"ABI "+names[i]);
        Console.WriteLine("18 protocol scenarios, 8 invalid DATs, SHA guard and Windows ABI passed ("+(IntPtr.Size*8)+" bits). No USB opened.");
    }
}
'@
Add-Type -TypeDefinition ((Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot '..\scripts\NativeFlasher.cs')) + $testSource)
[NativeFlasherTests]::Run()
