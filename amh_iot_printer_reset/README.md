# IoT Printer Reset (`amh_iot_printer_reset`)

A way to undo "don't ask again" when printing through Odoo IoT.

## The problem

When you print a report to an IoT printer, Odoo shows a dialog to pick the
printer, with an option to stop asking. Once that is ticked, that report goes
straight to the same printer every time with no dialog.

That's handy for something that always prints in one place, like shipping labels
at the shipping desk. But it's also one click to lose the choice of printer. If
someone ticks it by mistake, or picks the wrong printer, there's no button in
Odoo to get the dialog back.

The choice isn't stored in the database. Odoo IoT keeps it in **each computer's
browser** (localStorage, key `odoo-iot-linked_reports`). Normally the only fix is
to clear the browser's site data for your Odoo, which also logs you out and
clears everything else.

Our advice: leave the dialog on for most reports. It's one extra click to confirm
the printer, and it keeps the option open to print somewhere else. Use "don't ask
again" only where a report really always goes to one printer.

## What this module adds

- **User menu → Reset Printer Choices** (top right, under your name). Clears
  every saved printer choice in this browser. Each report asks which printer to
  use on its next print.
- **Reset my printer** button on each PDF report's settings form (*Settings →
  Technical → Actions → Reports*, open a report; developer mode needed). Clears
  the saved choice for just that report, in this browser.

Both act only on the computer and browser you click them from. Another user's
saved choice on another PC stays as it is, so have them click it on their own
machine.

## Install

Depends only on `base` and `web`. It doesn't depend on the `iot` app, so it
installs cleanly whether or not IoT is installed (it simply has nothing to reset
without IoT). Update the Apps list, search "IoT Printer Reset", install.

Built and used on Odoo 19 Enterprise (Odoo.sh) with Windows virtual IoT boxes.
