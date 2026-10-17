"""Browser end-to-end check against demo.py + `npm run dev` (needs playwright + chromium).
    python scripts/e2e_demo.py <screenshot-dir>"""
import sys, glob
from playwright.sync_api import sync_playwright
S = sys.argv[1]
exe = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")[0]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1100, "height": 900})
    logs = []
    pg.on("console", lambda m: logs.append(m.text) if m.type == "error" else None)
    pg.goto("http://localhost:5173"); pg.screenshot(path=f"{S}/1-login.png")
    pg.click("a.btn"); pg.wait_for_timeout(2500); print(pg.url); pg.screenshot(path=f"{S}/1b.png"); pg.wait_for_selector("button:has-text('Sync now')")
    pg.click("text=Sync now")
    pg.wait_for_function("document.querySelector('.stat b') && document.querySelector('.stat b').textContent !== '0' && document.querySelector('.stat b').textContent !== '–' && !document.querySelector('[role=status]')", timeout=30000)
    pg.screenshot(path=f"{S}/2-needs.png")
    pg.click("nav.tabs >> text=Groups"); pg.wait_for_selector("svg.bubbles circle")
    pg.screenshot(path=f"{S}/3-bubbles.png")
    n = pg.locator("svg.bubbles circle").count(); print("bubbles", n)
    pg.fill("input[placeholder='Sender…']", "swiggy"); pg.wait_for_timeout(800)
    print("after sender filter", pg.locator("svg.bubbles circle").count())
    pg.fill("input[placeholder='Sender…']", "")
    pg.wait_for_timeout(800)
    pg.locator("svg.bubbles circle").first.dispatch_event("click"); pg.wait_for_selector("text=All groups")
    pg.click("text=By subject"); pg.screenshot(path=f"{S}/4-subjects.png")
    pg.locator("text=/Select all \\d+/").first.click()
    print(pg.locator("text=/\\d+ selected/").first.inner_text())
    pg.click("text=trash selected"); pg.wait_for_selector("text=Move to Trash"); pg.screenshot(path=f"{S}/5-confirm.png")
    pg.click("button:has-text('Confirm')"); pg.wait_for_selector(".toast"); print("toast:", pg.inner_text(".toast"))
    pg.screenshot(path=f"{S}/6-after.png")
    pg.click(".toast >> text=Undo"); pg.wait_for_selector("text=Undone"); print("undo ok")
    print("errors:", logs)
    b.close()
