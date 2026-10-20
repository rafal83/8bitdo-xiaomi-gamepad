// MIT: independent Windows implementation of the documented 8BitDo protocol.
// Protocol reference: https://github.com/fwupd/fwupd/tree/main/plugins/ebitdo
// No vendor library is loaded. Probe sends identification requests only.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

public interface IBootHid : IDisposable {
    void Send(byte[] packet);
    byte[] Receive();
    bool IsPresent { get; }
}

public sealed class BootIdentity {
    public uint Version;
    public int VersionBytes;
    internal uint[] Id;
    public override string ToString() {
        return String.Format("Firmware {0}.{1:00} ; reponse version {2} octets ; identifiant materiel recu.",
                             Version / 100, Version % 100, VersionBytes);
    }
}

public static class NativeBootProtocol {
    private static readonly uint[] Keys = {
        0x186976e5,0xcac67acd,0x38f27fee,0x0a4948f1,0xb75b7753,0x1f8ffa5c,0xbff8cf43,0xc4936167,
        0x92bd03f0,0x5573c6ed,0x57d8845b,0x827197ac,0xb91901c9,0x3917edfe,0xbcd6344f,0xcf9e23b5
    };

    public static byte[] Packet(byte subtype, byte command, byte[] payload) {
        int size = payload == null ? 0 : payload.Length;
        if(size > 56) throw new ArgumentException("Paquet HID trop long.");
        var packet = new byte[64];
        packet[0] = (byte)(size == 0 ? 5 : size + 7);
        packet[2] = subtype;
        packet[3] = (byte)(size == 0 ? 1 : size + 3);
        packet[5] = command;
        packet[6] = (byte)size;
        if(size > 0) Buffer.BlockCopy(payload, 0, packet, 8, size);
        return packet;
    }

    private static void CheckFrame(byte[] reply, byte subtype) {
        // The RR bootloader uses different packet-length conventions for
        // version and ID replies. Validate each response shape separately.
        if(reply == null || reply.Length != 64 || reply[0] < 5 || reply[0] > 64 ||
           reply[1] != 0 || reply[2] != subtype || reply[4] != 0 || reply[3] > 60)
            throw new InvalidDataException("Reponse bootloader inattendue. Transfert arrete.");
    }

    private static void Acknowledge(IBootHid hid, bool final = false) {
        byte[] reply = hid.Receive();
        CheckFrame(reply, 0x16);
        bool plain = reply[5] == 0x14 && reply[6] == 0 && reply[7] == 0 &&
                     (reply[3] == 1 || reply[3] == 3) && reply[0] <= 8;
        // Observed on RR bootloader 2.07: the finalisation ACK carries one status byte equal to 1.
        bool status = final && reply[0] == 9 && reply[3] == 4 && reply[5] == 0x14 &&
                      reply[6] == 1 && reply[7] == 0 && reply[8] == 1;
        if(!plain && !status)
            throw new InvalidDataException("Le bootloader n'a pas acquitte la commande (reponse : " +
                BitConverter.ToString(reply, 0, 16) + "). Aucun renvoi automatique.");
    }

    public static BootIdentity Identify(IBootHid hid) {
        hid.Send(Packet(0x16, 4, null));
        byte[] version = hid.Receive();
        CheckFrame(version, 0x16);
        int size = version[6] | version[7] << 8;
        if(version[5] != 4 || (size != 4 && size != 8) || version[3] != size + 3 ||
           (version[0] != size + 7 && version[0] != size + 8))
            throw new InvalidDataException("Format de version bootloader non reconnu.");
        uint number = BitConverter.ToUInt32(version, 8);
        if(number > 99999) throw new InvalidDataException("Version bootloader incoherente.");
        hid.Send(Packet(0x1a, 0, null));
        byte[] uid = hid.Receive();
        CheckFrame(uid, 0x19);
        if(uid[3] != 36 || (uid[0] != 40 && uid[0] != 41 && uid[0] != 45))
            throw new InvalidDataException("Identifiant bootloader de taille incorrecte.");
        var id = new uint[3];
        for(int i = 0; i < 3; i++) id[i] = BitConverter.ToUInt32(uid, 5 + 4*i);
        if((id[0] | id[1] | id[2]) == 0 || (id[0] & id[1] & id[2]) == UInt32.MaxValue)
            throw new InvalidDataException("Identifiant bootloader vide.");
        return new BootIdentity { Version = number, VersionBytes = size, Id = id };
    }

    public static void ValidateDat(byte[] dat) {
        uint[] header = {207, 0x08003400, 0x18800, 0, 0, 0, 0};
        if(dat == null || dat.Length != 100380) throw new InvalidDataException("Taille DAT incorrecte.");
        for(int i = 0; i < header.Length; i++)
            if(BitConverter.ToUInt32(dat, i*4) != header[i])
                throw new InvalidDataException("Seul le DAT 2.07 de cet adaptateur est accepte.");
    }

    public static void Transfer(IBootHid hid, byte[] dat, Action<int> progress) {
        ValidateDat(dat); // Must run before any command, including identification.
        BootIdentity identity = Identify(hid);
        // Limit this experimental backend to the version used by this project.
        if(identity.Version != 207)
            throw new InvalidDataException("Version appareil non validee pour cet updater : " + identity.Version);
        progress(0);
        var header = new byte[28];
        Buffer.BlockCopy(dat, 0, header, 0, header.Length);
        hid.Send(Packet(0x16, 1, header));
        Acknowledge(hid);
        int previous = 0;
        for(int offset = 28; offset < dat.Length; offset += 32) {
            var block = new byte[Math.Min(32, dat.Length - offset)];
            Buffer.BlockCopy(dat, offset, block, 0, block.Length);
            hid.Send(Packet(0x16, 0, block));
            Acknowledge(hid); // Never retry: a timed-out write may already be committed.
            int percentage = (int)((long)(offset + block.Length - 28)*98/(dat.Length-28));
            if(percentage != previous) { progress(percentage); previous = percentage; }
        }
        var encoded = new byte[12];
        for(int i = 0; i < 3; i++) {
            uint value = identity.Id[i] ^ Keys[identity.Id[i] & 15];
            Buffer.BlockCopy(BitConverter.GetBytes(value), 0, encoded, i*4, 4);
        }
        hid.Send(Packet(0x16, 6, encoded)); // This command has no ACK in this protocol.
        progress(99);
        hid.Send(Packet(0x16, 2, null));
        try {
            Acknowledge(hid, true);
        } catch(TimeoutException) {
            if(hid.IsPresent) throw;
            Console.WriteLine("Adaptateur deconnecte apres la commande de finalisation.");
        } catch(IOException) {
            // A malformed response is an error even if USB subsequently disappears.
            throw;
        } catch(Win32Exception) {
            if(hid.IsPresent) throw;
            Console.WriteLine("Adaptateur deconnecte apres la commande de finalisation.");
        }
        progress(100);
    }
}

public static class NativeFlasher {
    public static string Probe() {
        using(var hid = WindowsBootHid.OpenSingle())
            return NativeBootProtocol.Identify(hid).ToString();
    }
    public static void Run(string firmware, string expectedSha256) {
        byte[] dat = File.ReadAllBytes(firmware);
        NativeBootProtocol.ValidateDat(dat);
        using(var sha = SHA256.Create()) {
            string actual = BitConverter.ToString(sha.ComputeHash(dat)).Replace("-", "");
            if(!String.Equals(actual, expectedSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Le firmware a change avant le transfert.");
        }
        using(var hid = WindowsBootHid.OpenSingle())
            NativeBootProtocol.Transfer(hid, dat, delegate(int p) { Console.WriteLine("Progression : " + p + " %"); });
        Console.WriteLine("Transfert integre termine. Rebrancher normalement et tester sur Switch.");
    }
}

// Windows inbox HID driver, Unicode paths, x86/x64 structure sizes and timed I/O.
public sealed class WindowsBootHid : IBootHid {
    private readonly SafeFileHandle handle;
    private readonly string path;
    private WindowsBootHid(string path) {
        this.path = path;
        handle = CreateFileW(path, 0xc0000000, 3, IntPtr.Zero, 3, 0x40000000, IntPtr.Zero);
        if(handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error(), "Ouverture HID impossible."); }
        try { ValidateHandle(handle); } catch { handle.Dispose(); throw; }
    }
    public static WindowsBootHid OpenSingle() {
        var candidates = new List<string>();
        foreach(string path in Paths()) {
            if(path.IndexOf("vid_0483&pid_5760", StringComparison.OrdinalIgnoreCase) < 0) continue;
            using(var metadata = CreateFileW(path, 0, 3, IntPtr.Zero, 3, 0, IntPtr.Zero)) {
                if(metadata.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error(), "Adaptateur HID inaccessible.");
                ValidateHandle(metadata);
                candidates.Add(path);
            }
        }
        if(candidates.Count != 1)
            throw new InvalidOperationException("Un seul adaptateur 8BitDo 0483:5760 en mode mise a jour est requis (trouve : " + candidates.Count + ").");
        return new WindowsBootHid(candidates[0]);
    }
    public bool IsPresent {
        get { foreach(string current in Paths()) if(String.Equals(current, path, StringComparison.OrdinalIgnoreCase)) return true; return false; }
    }
    public void Send(byte[] packet) {
        if(packet == null || packet.Length != 64) throw new ArgumentException("Paquet HID de 64 octets requis.");
        var report = new byte[65]; // HID report ID 0 precedes the protocol packet.
        Buffer.BlockCopy(packet, 0, report, 1, 64);
        Transfer(report, true);
    }
    public byte[] Receive() {
        var report = new byte[65];
        Transfer(report, false);
        if(report[0] != 0) throw new InvalidDataException("Identifiant de rapport HID inattendu.");
        var packet = new byte[64];
        Buffer.BlockCopy(report, 1, packet, 0, 64);
        return packet;
    }
    private void Transfer(byte[] report, bool writing) {
        IntPtr buffer = Marshal.AllocHGlobal(65);
        IntPtr pointer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Overlapped)));
        using(var signal = new EventWaitHandle(false, EventResetMode.ManualReset)) {
            bool pending = false;
            try {
                Marshal.Copy(report, 0, buffer, 65);
                var overlapped = new Overlapped { Event = signal.SafeWaitHandle.DangerousGetHandle() };
                Marshal.StructureToPtr(overlapped, pointer, false);
                bool done = writing ? WriteFile(handle, buffer, 65, IntPtr.Zero, pointer)
                                    : ReadFile(handle, buffer, 65, IntPtr.Zero, pointer);
                if(!done && Marshal.GetLastWin32Error() != 997) throw new Win32Exception(Marshal.GetLastWin32Error());
                pending = !done;
                if(pending && !signal.WaitOne(5000)) {
                    CancelIoEx(handle, pointer);
                    uint ignored;
                    GetOverlappedResult(handle, pointer, out ignored, true); // Drain cancellation before freeing memory.
                    pending = false;
                    throw new TimeoutException("Delai HID depasse. Le paquet n'est pas renvoye.");
                }
                uint count;
                bool success = GetOverlappedResult(handle, pointer, out count, true);
                pending = false;
                if(!success) throw new Win32Exception(Marshal.GetLastWin32Error());
                if(count != 65) throw new InvalidDataException("Rapport HID tronque.");
                if(!writing) Marshal.Copy(buffer, report, 0, 65);
            } finally {
                if(pending) { CancelIoEx(handle, pointer); uint ignored; GetOverlappedResult(handle, pointer, out ignored, true); }
                Marshal.FreeHGlobal(pointer);
                Marshal.FreeHGlobal(buffer);
            }
        }
    }
    public void Dispose() { handle.Dispose(); }

    private static void ValidateHandle(SafeFileHandle handle) {
        var attributes = new Attributes { Size = Marshal.SizeOf(typeof(Attributes)) };
        if(!HidD_GetAttributes(handle, ref attributes) || attributes.Vendor != 0x0483 || attributes.Product != 0x5760)
            throw new InvalidDataException("Identite USB incompatible.");
        var vendor = new StringBuilder(128);
        if(!HidD_GetManufacturerString(handle, vendor, 256) ||
           !vendor.ToString().StartsWith("8BitDo", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Fabricant HID incompatible.");
        IntPtr preparsed;
        if(!HidD_GetPreparsedData(handle, out preparsed)) throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            Caps caps;
            if(HidP_GetCaps(preparsed, out caps) != 0x110000 || caps.Input != 65 || caps.Output != 65)
                throw new InvalidDataException("Format HID bootloader non reconnu (65 octets attendus).");
        } finally { HidD_FreePreparsedData(preparsed); }
    }

    private static string[] Paths() {
        Guid guid;
        HidD_GetHidGuid(out guid);
        IntPtr set = SetupDiGetClassDevsW(ref guid, IntPtr.Zero, IntPtr.Zero, 0x12);
        if(set == new IntPtr(-1)) throw new Win32Exception(Marshal.GetLastWin32Error());
        var paths = new List<string>();
        try {
            for(uint index = 0; ; index++) {
                var data = new InterfaceData { Size = Marshal.SizeOf(typeof(InterfaceData)) };
                if(!SetupDiEnumDeviceInterfaces(set, IntPtr.Zero, ref guid, index, ref data)) {
                    int error = Marshal.GetLastWin32Error();
                    if(error == 259) break;
                    throw new Win32Exception(error);
                }
                uint needed;
                SetupDiGetDeviceInterfaceDetailW(set, ref data, IntPtr.Zero, 0, out needed, IntPtr.Zero);
                if(needed < 6 || needed > 65536) throw new InvalidDataException("Chemin HID de taille incorrecte.");
                IntPtr detail = Marshal.AllocHGlobal((int)needed);
                try {
                    Marshal.WriteInt32(detail, IntPtr.Size == 8 ? 8 : 6);
                    if(!SetupDiGetDeviceInterfaceDetailW(set, ref data, detail, needed, out needed, IntPtr.Zero))
                        throw new Win32Exception(Marshal.GetLastWin32Error());
                    paths.Add(Marshal.PtrToStringUni(IntPtr.Add(detail, 4)));
                } finally { Marshal.FreeHGlobal(detail); }
            }
        } finally { SetupDiDestroyDeviceInfoList(set); }
        return paths.ToArray();
    }

    [StructLayout(LayoutKind.Sequential)] private struct Overlapped { public UIntPtr Internal, InternalHigh; public uint Offset, OffsetHigh; public IntPtr Event; }
    [StructLayout(LayoutKind.Sequential)] private struct InterfaceData { public int Size; public Guid Guid; public uint Flags; public UIntPtr Reserved; }
    [StructLayout(LayoutKind.Sequential)] private struct Attributes { public int Size; public ushort Vendor, Product, Version; }
    [StructLayout(LayoutKind.Sequential)] private struct Caps {
        public ushort Usage, UsagePage, Input, Output, Feature;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst=17)] public ushort[] Reserved;
        public ushort Nodes, InputButtons, InputValues, InputIndices, OutputButtons, OutputValues, OutputIndices, FeatureButtons, FeatureValues, FeatureIndices;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern bool ReadFile(SafeFileHandle handle, IntPtr buffer, uint size, IntPtr count, IntPtr overlapped);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern bool WriteFile(SafeFileHandle handle, IntPtr buffer, uint size, IntPtr count, IntPtr overlapped);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetOverlappedResult(SafeFileHandle handle, IntPtr overlapped, out uint count, bool wait);
    [DllImport("kernel32.dll", SetLastError=true)] private static extern bool CancelIoEx(SafeFileHandle handle, IntPtr overlapped);
    [DllImport("hid.dll")] private static extern void HidD_GetHidGuid(out Guid guid);
    [DllImport("hid.dll", SetLastError=true)] [return:MarshalAs(UnmanagedType.U1)] private static extern bool HidD_GetAttributes(SafeFileHandle handle, ref Attributes attributes);
    [DllImport("hid.dll", CharSet=CharSet.Unicode, SetLastError=true)] [return:MarshalAs(UnmanagedType.U1)] private static extern bool HidD_GetManufacturerString(SafeFileHandle handle, StringBuilder text, uint bytes);
    [DllImport("hid.dll", SetLastError=true)] [return:MarshalAs(UnmanagedType.U1)] private static extern bool HidD_GetPreparsedData(SafeFileHandle handle, out IntPtr data);
    [DllImport("hid.dll")] [return:MarshalAs(UnmanagedType.U1)] private static extern bool HidD_FreePreparsedData(IntPtr data);
    [DllImport("hid.dll")] private static extern int HidP_GetCaps(IntPtr data, out Caps caps);
    [DllImport("setupapi.dll", CharSet=CharSet.Unicode, SetLastError=true)] private static extern IntPtr SetupDiGetClassDevsW(ref Guid guid, IntPtr enumerator, IntPtr parent, uint flags);
    [DllImport("setupapi.dll", SetLastError=true)] private static extern bool SetupDiEnumDeviceInterfaces(IntPtr set, IntPtr device, ref Guid guid, uint index, ref InterfaceData data);
    [DllImport("setupapi.dll", CharSet=CharSet.Unicode, SetLastError=true)] private static extern bool SetupDiGetDeviceInterfaceDetailW(IntPtr set, ref InterfaceData data, IntPtr detail, uint size, out uint needed, IntPtr device);
    [DllImport("setupapi.dll")] private static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);
}
