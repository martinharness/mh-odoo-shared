# DYMO labels on the Odoo Windows IoT: size and orientation fix

**This is not an Odoo module.** Odoo.sh ignores this folder (it has no
`__manifest__.py`). It's a PowerShell patch you install on each Windows PC that
runs the Odoo IoT and has a DYMO attached.

If your DYMOs already print correctly through IoT, you don't need any of this.

## The symptom

DYMO labels printed from Odoo through IoT came out **half size and/or sideways**.
The PDF Odoo generated was correct (57 x 32 mm for a 2-1/4" x 1-1/4" label).
Setting the paper size and orientation in the DYMO's Windows *Printing
Preferences* made no difference.

## Why

The Windows IoT doesn't print PDFs through Windows' normal print dialog. It
saves the PDF and runs its bundled **SumatraPDF** with:

```
SumatraPDF.exe -print-to "<printer>" document.pdf -silent -print-settings duplex
```

With those settings SumatraPDF doesn't use the paper size and orientation set in
the DYMO's Windows printer defaults. It falls back to its own guess, which is
wrong for a small landscape label.

## The fix (two parts)

### 1. Patch the IoT printer driver (scripts in this folder)

`iot-dymo-patch.ps1` edits the IoT's
`C:\Program Files\Odoo IoT <version>\odoo\addons\iot_drivers\iot_handlers\drivers\printer_driver_W.py`
so that any printer with **"DYMO" in its name** is printed with

```
-print-settings "paper=30334 2-1/4 in x 1-1/4 in,landscape"
```

instead of `duplex`. Every other printer prints exactly as before. So name your
DYMOs in Windows with "DYMO" in the name (we use *DYMO Shipping*, *DYMO
Counter1* and so on, which also makes them easy to tell apart in Odoo).

The IoT **resets its own code when it updates itself, about weekly**, which wipes
the patch. So the installer also registers a scheduled task, *AMH IoT DYMO
patch*. It runs as SYSTEM at startup and every hour, puts the patch back if it's
gone, and restarts the `odoo-iot` service only when it had to re-patch. If the
patch is already there, it does nothing.

**Install** on each PC, in PowerShell opened with *Run as Administrator*, from
this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-dymo-patch.ps1
```

If your labels aren't the 30334 size, pass the paper name exactly as the DYMO
driver lists it (*Printer properties > Printing Preferences > Advanced > Paper
Size*):

```powershell
powershell -ExecutionPolicy Bypass -File .\install-dymo-patch.ps1 -PaperName "30252 Address"
```

The log is at `C:\ProgramData\AMH\iot-dymo-patch.log`, and the original driver
file is kept next to it as `printer_driver_W.py.orig`.

### 2. Run the IoT service as the PC's user, not Local System

The patch alone wasn't enough for us. The exact same SumatraPDF command printed
correctly when run as the logged-in Windows user, and wrong when run as Local
System (the service's default account). Local System doesn't see the user's DYMO
printer setup.

So on each PC the **odoo-iot** Windows service logs on as that PC's local user:

1. Open `services.msc`, then **odoo-iot**, then **Log On**.
2. Choose **This account** and enter `.\<windows user name>`. Run `whoami` to
   see it; the part after the `\` is the name. Use the account's real
   **password**, not the Windows Hello PIN.
3. Apply, then restart the service.

Gotchas:
- If you get error **1069** (logon failure), the account name or password is
  wrong. On one of our PCs the local account was simply called "User", not the
  person's name.
- Changing that Windows password breaks the service logon. Re-enter it in the
  service.
- Reinstalling the IoT resets the service to Local System. Redo step 2, and run
  the installer again.

## Also worth knowing

- **Don't** turn on *Download custom handlers from the database* on the IoT box.
  We tried shipping this fix as an Odoo module that way. It pulled the database's
  older Odoo 19 IoT files over the IoT's newer ones and broke printer detection.
  That's why this is a local script and not a module.
- Our IoT is the **version 20** Windows installer (there was no 19 installer),
  paired to an Odoo 19 database. That's the driver code this patch matches. If
  Odoo changes that part of the driver, the script finds nothing to patch,
  writes that to the log, and leaves the file alone.
- Odoo paper format for the label report: 57 mm wide x 32 mm high, Portrait,
  all margins 0, DPI 96, smart shrinking off. The *landscape* in the print
  settings is what turns it to feed through the DYMO correctly.
- Label templates built on `web.html_container` need a `<div class="article">`
  wrapper. Without it, accents and dashes print as garbage (Ã©, â€“).
