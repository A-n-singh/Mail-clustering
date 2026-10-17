"""Browser check of the topic map (needs the demo backend and npm run dev running, plus playwright)."""
import sys, glob
from playwright.sync_api import sync_playwright
S = sys.argv[1]
exe = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")[0]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1100, "height": 1000})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:5173"); pg.click("a.btn"); pg.wait_for_selector("button:has-text('Sync now')")
    pg.click("button:has-text('Sync now')")
    pg.wait_for_function("document.querySelector('.stat b').textContent > 0 && !document.querySelector('[role=status]')", timeout=60000)
    pg.click("nav.tabs >> text=Topic map")
    pg.wait_for_selector("canvas", timeout=240000); print("map shown")
    pg.wait_for_timeout(500); pg.screenshot(path=f"{S}/8-map.png")
    box = pg.locator("canvas").bounding_box()

    found = None
    for fy in range(40, int(box["height"]) - 20, 12):
        for fx in range(40, int(box["width"]) - 20, 12):
            pg.mouse.move(box["x"] + fx, box["y"] + fy)
            if pg.locator(".tip").count():
                found = pg.inner_text(".tip"); break
        if found: break
    print("tooltip:", (found or "NONE").replace("\n", " | "))

    def lasso_all():
        if pg.get_attribute("button[aria-pressed]", "aria-pressed") != "true":
            pg.click("button:has-text('Lasso select')")
        pg.mouse.move(box["x"] + 10, box["y"] + 10); pg.mouse.down()
        for fx, fy in [(.99, .02), (.99, .98), (.01, .98), (.01, .02)]:
            pg.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy, steps=4)
        pg.mouse.up(); pg.wait_for_selector("text=/\\d+ selected/")

    lasso_all(); print("lasso all:", pg.inner_text("text=/\\d+ selected/"))
    pg.click("button.danger:has-text('trash')"); pg.wait_for_selector("text=Move to Trash")
    print("blocked msg shown:", pg.locator("text=Blocked").count() == 1, "| confirm disabled:", pg.locator("button:has-text('Confirm')").is_disabled())
    pg.screenshot(path=f"{S}/9-lasso.png"); pg.click("button:has-text('Cancel')")
    pg.click("button:has-text('Clear')")
    pg.click(".topic-legend >> text=/shipped/"); lasso_all(); print("lasso one topic:", pg.inner_text("text=/\\d+ selected/"))
    pg.screenshot(path=f"{S}/10-topic.png")
    pg.click("button.danger:has-text('trash')"); pg.wait_for_selector("text=Move to Trash")
    print("confirm:", pg.inner_text(".modal h3"))
    pg.click("button:has-text('Confirm')"); pg.wait_for_selector(".toast"); print("toast:", pg.inner_text(".toast").split("\n")[0])
    pg.click(".toast >> text=Undo"); pg.wait_for_selector("text=Undone"); print("undo ok")
    print("page errors:", errs)
    b.close()
