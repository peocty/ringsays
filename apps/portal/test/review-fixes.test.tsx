import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { Button, Dialog } from "../src/components/ui";
import { ar } from "../src/i18n/ar";
import { initI18n } from "../src/i18n";
import { fileNameFrom } from "../src/lib/download";
import { zonedToIso } from "../src/lib/format";

beforeAll(() => {
  initI18n("en");
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});

describe("stage 5 portal review fixes", () => {
  it("date filters are read in the configured zone, not the browser's", () => {
    expect(zonedToIso("2026-10-04T10:30", "Asia/Riyadh")).toBe("2026-10-04T07:30:00.000Z");
    expect(zonedToIso("2026-10-04T10:30", "Asia/Kolkata")).toBe("2026-10-04T05:00:00.000Z");
    expect(zonedToIso("2026-03-29T03:30", "Europe/London")).toBe("2026-03-29T02:30:00.000Z");
    expect(zonedToIso("nonsense", "Asia/Riyadh")).toBeNull();
  });

  it("download names prefer RFC 5987 filename*", () => {
    expect(fileNameFrom(`attachment; filename="x.pdf"; filename*=UTF-8''%D8%B3%D8%AC%D9%84.pdf`)).toBe("سجل.pdf");
    expect(fileNameFrom(`attachment; filename="audit.csv"`)).toBe("audit.csv");
    expect(fileNameFrom("")).toBeNull();
  });

  it("Arabic destructive labels never equal Cancel", () => {
    const cancel = ar.common.cancel;
    for (const label of [ar.integration.revoke, ar.integration.disable, ar.organisation.revoke, ar.catalogue.retire,
      ar.backoffice.suspend, ar.backoffice.reject]) {
      expect(label).not.toBe(cancel);
    }
  });

  it("closing a dialog returns focus to the button that opened it", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <Button onClick={() => setOpen(true)}>Open</Button>
          <Dialog open={open} title="Edit" onClose={() => setOpen(false)}>
            <input aria-label="Name" />
            <Button onClick={() => setOpen(false)}>Done</Button>
          </Dialog>
        </>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    await userEvent.click(opener);
    expect(document.activeElement).toBe(screen.getByLabelText("Name")); // first field, not the close button
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(document.activeElement).toBe(opener);
  });
});
