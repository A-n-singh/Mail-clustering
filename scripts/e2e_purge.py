"""Browser check of the permanent-delete flow: start the demo backend with ALLOW_PERMANENT_DELETE=true and npm run dev first."""
import sys, glob
from playwright.sync_api import sync_playwright
S = sys.argv[1]
exe = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")[0]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1100, "height": 800})
    pg.goto("http://localhost:5173"); pg.click("a.btn"); pg.wait_for_selector("button:has-text('Sync now')")
    pg.click("button:has-text('Sync now')")
    pg.wait_for_function("document.querySelector('.stat b').textContent > 0 && !document.querySelector('[role=status]')", timeout=30000)
    pg.click("nav.tabs >> text=Groups"); pg.wait_for_selector("svg.bubbles circle")
    pg.locator("svg.bubbles circle").first.dispatch_event("click"); pg.wait_for_selector("text=All groups")
    pg.click("text=Select all"); 
    pg.click("button:has-text('trash selected')"); pg.click("button:has-text('Confirm')"); pg.wait_for_selector(".toast")
    pg.click("nav.tabs >> text=Trash"); pg.wait_for_selector("text=in Trash"); print(pg.inner_text(".card").split("\n")[0])
    pg.click("button:has-text('forever…')")
    print("delete button disabled before typing:", pg.locator("button:has-text('Delete forever')").is_disabled())
    pg.fill("input[aria-label=confirmation]", "DELETE FOREVER"); pg.screenshot(path=f"{S}/7-purge.png")
    pg.click("button:has-text('Delete forever')"); pg.wait_for_selector(".toast >> text=Permanently")
    print(pg.inner_text(".toast").split("\n")[0]); print(pg.inner_text(".card").split("\n")[0])
    b.close()
