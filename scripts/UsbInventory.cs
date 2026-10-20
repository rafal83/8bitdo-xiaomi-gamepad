// Read-only SetupAPI enumeration; no HID handles or vendor DLL are opened.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class UsbInventory
{
    [StructLayout(LayoutKind.Sequential)]
    private struct DeviceInfo {
        public uint cbSize;
        public Guid ClassGuid;
        public uint DevInst;
        public UIntPtr Reserved;
    }
    [DllImport("setupapi.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern IntPtr SetupDiGetClassDevsW(IntPtr guid, string enumerator, IntPtr parent, uint flags);
    [DllImport("setupapi.dll", SetLastError=true)]
    private static extern bool SetupDiEnumDeviceInfo(IntPtr set, uint index, ref DeviceInfo info);
    [DllImport("setupapi.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern bool SetupDiGetDeviceInstanceIdW(IntPtr set, ref DeviceInfo info, StringBuilder id, int size, out int needed);
    [DllImport("setupapi.dll")]
    private static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);

    public static string[] GetPresent() {
        IntPtr set = SetupDiGetClassDevsW(IntPtr.Zero, "USB", IntPtr.Zero, 2 | 4);
        if(set == new IntPtr(-1)) throw new Win32Exception(Marshal.GetLastWin32Error());
        var result = new List<string>();
        try {
            for(uint index=0; ; index++) {
                var info = new DeviceInfo();
                info.cbSize = (uint)Marshal.SizeOf(typeof(DeviceInfo));
                if(!SetupDiEnumDeviceInfo(set, index, ref info)) {
                    int error = Marshal.GetLastWin32Error();
                    if(error == 259) break;
                    throw new Win32Exception(error);
                }
                var id = new StringBuilder(4096);
                int needed;
                if(!SetupDiGetDeviceInstanceIdW(set, ref info, id, id.Capacity, out needed))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                result.Add(id.ToString());
            }
        } finally { SetupDiDestroyDeviceInfoList(set); }
        return result.ToArray();
    }
}
