"""Browser check of the 3D hologram (start the demo backend and npm run dev first; needs playwright)."""
import sys, glob
from playwright.sync_api import sync_playwright
S = sys.argv[1]
exe = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")[0]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe, args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    pg = b.new_page(viewport={"width": 1280, "height": 1100})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "401" not in m.text and "404" not in m.text else None)
    pg.goto("http://localhost:5173"); pg.click("a.btn"); pg.wait_for_selector("button:has-text('Sync now')")
    pg.click("button:has-text('Sync now')")
    pg.wait_for_function("document.querySelector('.gauge b') && document.querySelector('.gauge b').textContent > 0 && !document.querySelector('[role=status]')", timeout=90000)
    pg.wait_for_selector("canvas", timeout=240000); print("hologram canvas shown")
    pg.wait_for_timeout(2500); pg.screenshot(path=f"{S}/h1-hologram.png")
    pg.wait_for_timeout(3000); pg.screenshot(path=f"{S}/h2-hologram-rot.png")
    box = pg.locator(".holo").bounding_box()
    found = None
    for fy in range(100, int(box["height"]) - 60, 14):
        for fx in range(80, int(box["width"]) - 80, 14):
            pg.mouse.move(box["x"] + fx, box["y"] + fy)
            if pg.locator(".holo-tip").count():
                found = pg.inner_text(".holo-tip"); break
        if found: break
    print("hover:", (found or "NONE").replace("\n", " | "))
    pg.screenshot(path=f"{S}/h3-hover.png")
    pg.click("button:has-text('Lasso')"); 
    pg.mouse.move(box["x"] + 60, box["y"] + 60); pg.mouse.down()
    for fx, fy in [(.97, .06), (.97, .94), (.03, .94), (.03, .06)]:
        pg.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy, steps=4)
    pg.mouse.up(); pg.wait_for_selector("text=/\\d+ selected/"); print("lasso:", pg.inner_text("text=/\\d+ selected/"))
    pg.screenshot(path=f"{S}/h4-lasso.png")
    pg.click("nav.tabs >> text=Groups"); pg.wait_for_selector("svg.bubbles circle"); pg.wait_for_timeout(500); pg.screenshot(path=f"{S}/h5-groups.png")
    pg.click("nav.tabs >> text=Needs action"); pg.wait_for_timeout(800); pg.screenshot(path=f"{S}/h6-needs.png")
    print("errors:", errs[:5])
    b.close()
