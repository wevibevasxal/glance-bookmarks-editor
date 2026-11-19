// Browser test of the editor UI + Glance Home screenshots (system Chrome via playwright-core).
//   node ui-test.mjs home <glanceUrl> <outPrefix>
//   node ui-test.mjs editor <editorUrl> <outDir> <password>
import { chromium } from "playwright-core";
import fs from "node:fs";

const [mode, url, out, password] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const fail = (msg) => { console.error("FAIL:", msg); process.exitCode = 1; };
const ok = (msg) => console.log("ok  ", msg);

async function home() {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}.png`, fullPage: true });
  const html = await page.$$eval(".widget-type-bookmarks", (els) => els.map((e) => e.outerHTML).join("\n"));
  fs.writeFileSync(`${out}.bookmarks.html`, html);
  const tiles = await page.$$eval(".widget-type-bookmarks a", (as) => as.map((a) => `${a.textContent.trim()} -> ${a.getAttribute("href")}`));
  fs.writeFileSync(`${out}.tiles.txt`, tiles.join("\n") + "\n");
  console.log(`home: ${tiles.length} tiles, screenshot ${out}.png`);
}

async function editor() {
  fs.mkdirSync(out, { recursive: true });
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const page = await ctx.newPage();
  const problems = [];
  // expected 401s: the session probe before login / after sign-out, and the deliberate wrong password
  const expected401 = /\/api\/(session|login)$/;
  page.on("response", (r) => {
    if (r.status() >= 400 && !(r.status() === 401 && expected401.test(new URL(r.url()).pathname)))
      problems.push(`HTTP ${r.status()} ${r.url()}`);
  });
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) problems.push(m.text()); });
  page.on("pageerror", (e) => problems.push(String(e)));
  const shot = (name) => page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
  // click Save, wait for that save to finish, return the toast it produced
  const save = async (re) => {
    // drop older toasts first: they expire on their own and would make a count-based wait racy
    await page.locator(".toast").evaluateAll((els) => els.forEach((e) => e.remove()));
    await page.click("#save");
    await page.locator("#save", { hasText: /^Save$/ }).waitFor({ state: "attached", timeout: 30000 });
    const t = page.locator(".toast", { hasText: re }).last();
    await t.waitFor({ timeout: 30000 });
    return (await t.textContent()).trim();
  };
  const toast = async (re) => {
    const t = page.locator(".toast", { hasText: re }).last();
    await t.waitFor({ timeout: 20000 });
    return (await t.textContent()).trim();
  };

  await page.goto(url);
  await page.locator("#login").waitFor({ state: "visible" });
  await shot("01-login");
  await page.fill("#login-form [name=password]", "wrong password!!");
  await page.click("#login-form button[type=submit]");
  await page.locator("#login-msg", { hasText: "wrong" }).waitFor();
  ok("wrong password refused in the UI");
  await page.fill("#login-form [name=password]", password);
  await page.click("#login-form button[type=submit]");
  await page.locator("#app").waitFor({ state: "visible" });
  await page.locator(".group").first().waitFor();
  ok(`signed in; ${await page.locator(".group").count()} groups on Bookmarks`);
  await shot("02-tiles-bookmarks");

  // add a tile with a selfh.st icon
  await page.click("#add-link");
  await page.fill("#link-form [name=title]", "Immich");
  await page.fill("#link-form [name=url]", "https://photos.example.com/");
  await page.fill("#lf-search", "immich");
  await page.locator("#lf-results .icon-opt[data-name=immich]").waitFor({ timeout: 20000 });
  await page.waitForTimeout(1200);  // let previews load for the screenshot
  await page.locator("#lf-results .icon-opt[data-name=immich]").click();
  await shot("03-add-tile-dialog");
  await page.click("#link-ok");
  await page.locator("#link-dlg").waitFor({ state: "hidden", timeout: 20000 });
  const row = page.locator(".link", { hasText: "Immich" });
  await row.waitFor();
  if (!(await page.locator("#savebar").isVisible())) fail("save bar not shown after an edit");
  ok("tile added (icon downloaded on Add)");

  // drag it to the top of its group
  const firstGroup = page.locator(".group").first();
  const firstRow = firstGroup.locator(".link").first();
  await row.locator(".grip").dragTo(firstRow.locator(".grip"), { targetPosition: { x: 3, y: 2 } });
  await page.waitForTimeout(400);
  const order = await firstGroup.locator(".link-title").allTextContents();
  if (order[0] !== "Immich") fail(`drag to top failed: ${order.join(", ")}`); else ok(`dragged to top: ${order.join(", ")}`);
  await shot("04-tiles-unsaved");
  ok("save: " + await save(/Tiles/));
  if (await page.locator("#savebar").isVisible()) fail("save bar still visible after save");

  // drag a group to the end
  const names = async () => page.locator(".group-title").allTextContents();
  const before = await names();
  await page.locator(".group").first().locator(".group-head .grip").dragTo(page.locator(".group").last(), { targetPosition: { x: 200, y: 60 } });
  await page.waitForTimeout(400);
  const after = await names();
  if (after[after.length - 1] !== before[0]) fail(`group drag failed: ${before} -> ${after}`); else ok(`group reordered: ${after.join(", ")}`);

  // edit: move Immich to the Apps widget's first group via the form
  await page.locator(".link", { hasText: "Immich" }).locator("button[title=Edit]").click();
  const opt = await page.locator("#link-form [name=group] optgroup[label=Apps] option").first().getAttribute("value");
  await page.selectOption("#link-form [name=group]", opt);
  await page.click("#link-form details.advanced summary");
  await page.fill("#link-form [name=description]", "Photos");
  await page.click("#link-ok");
  await page.locator("#link-dlg").waitFor({ state: "hidden" });
  await page.locator("#widget-tabs button.active", { hasText: "Apps" }).waitFor();
  ok("tile moved to Apps via the form");
  ok("save: " + await save(/Tiles/));
  await shot("05-tiles-apps");

  // invalid URL is refused in the form
  await page.click("#add-link");
  await page.fill("#link-form [name=title]", "Bad");
  await page.fill("#link-form [name=url]", "javascript:alert(1)");
  await page.click("#link-ok");
  await page.locator("#link-msg", { hasText: "http" }).waitFor();
  ok("invalid URL refused in the form");
  await page.click("#link-dlg [data-close]");

  // group add + rename
  await page.click("#add-group");
  await page.fill("#group-form [name=title]", "Zz test group");
  await page.click("#group-form button[type=submit]");
  await page.locator(".group-title", { hasText: /^Zz test group$/ }).waitFor();
  await page.locator(".group", { has: page.locator(".group-title", { hasText: /^Zz test group$/ }) }).locator("button[title='Rename group']").click();
  await page.fill("#group-form [name=title]", "Zz test group 2");
  await page.click("#group-form button[type=submit]");
  await page.locator(".group-title", { hasText: "Zz test group 2" }).waitFor();
  await page.locator(".group", { hasText: "Zz test group 2" }).locator("button[title='Delete group']").click();
  await page.locator(".group-title", { hasText: "Zz test group 2" }).waitFor({ state: "detached" });
  ok("group add, rename, delete (empty: no confirm)");
  if (await page.locator("#savebar").isVisible()) fail("adding+removing a group should leave no unsaved changes");

  // sections: add, add a group to it, rename, drag its tab to the front, delete
  const tabs = async () => page.locator("#widget-tabs button").allTextContents();
  const tabsBefore = await tabs();
  await page.click("#add-section");
  await page.fill("#group-form [name=title]", "Media");
  await page.click("#group-form button[type=submit]");
  await page.locator("#widget-tabs button.active", { hasText: "Media" }).waitFor();
  await page.locator("#groups .hint", { hasText: "No groups" }).waitFor();
  await page.click("#add-group");
  await page.fill("#group-form [name=title]", "Watch");
  await page.click("#group-form button[type=submit]");
  await page.locator(".group-title", { hasText: "Watch" }).waitFor();
  await page.click("#rename-section");
  await page.fill("#group-form [name=title]", "Media 2");
  await page.click("#group-form button[type=submit]");
  await page.locator("#section-name", { hasText: "Media 2" }).waitFor();
  await page.locator("#widget-tabs button", { hasText: "Media 2" }).dragTo(page.locator("#widget-tabs button").first(), { targetPosition: { x: 3, y: 5 } });
  await page.waitForTimeout(400);
  const tabsMoved = await tabs();
  if (tabsMoved[0] !== "Media 2") fail(`section drag failed: ${tabsMoved.join(", ")}`); else ok(`sections: ${tabsMoved.join(", ")}`);
  await shot("05b-sections");
  if (!(await page.locator("#savebar").isVisible())) fail("save bar not shown after adding a section");
  await page.locator("#widget-tabs button", { hasText: "Media 2" }).click();
  await page.click("#delete-section");
  await page.click("#confirm-ok");
  await page.locator("#widget-tabs button", { hasText: "Media 2" }).waitFor({ state: "detached" });
  if ((await tabs()).join("|") !== tabsBefore.join("|")) fail(`sections after delete: ${await tabs()}`);
  if (await page.locator("#savebar").isVisible()) fail("adding+removing a section should leave no unsaved changes");
  ok("section add, group in it, rename, drag, delete");

  // countdown
  await page.click("#nav button[data-view=countdown]");
  await page.locator("#ev-add").waitFor();
  await page.click("#ev-add");
  const ev = page.locator("#ev-list .cd-row").last();
  await ev.locator("input").first().fill("Concert");
  const sels = ev.locator("select");
  await sels.nth(0).selectOption("9");     // month
  await sels.nth(1).selectOption("20");    // day
  await sels.nth(2).selectOption("once");  // repeats
  await ev.locator("input[type=number]").fill("2027");
  await ev.locator(".emoji-picks button", { hasText: "🎉" }).click();
  await shot("06-countdown");
  const left = await page.locator("#ev-left").textContent();
  ok(`countdown slots: ${left}`);
  ok("save: " + await save(/Countdown/));

  // icons
  await page.click("#nav button[data-view=icons]");
  await page.locator(".lib-item").first().waitFor();
  const used = page.locator(".lib-item", { hasText: "immich.webp" });
  if (!(await used.locator("button", { hasText: "Delete" }).isDisabled())) fail("in-use icon can be deleted");
  else ok("in-use icon: Delete disabled");
  await page.fill("#lib-search", "jellyfin");
  await page.locator("#lib-results .icon-opt").first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(1200);
  await shot("07-icons");

  // history: view + restore the newest tiles version
  await page.click("#nav button[data-view=history]");
  await page.locator("#hist-links .hist-row").first().waitFor();
  await page.locator("#hist-links .hist-row").first().locator("button", { hasText: "View" }).click();
  await page.locator("#confirm-pre").waitFor();
  await shot("08-history-diff");
  await page.click("#confirm-ok");
  await page.locator("#hist-links .hist-row").first().locator("button", { hasText: "Restore" }).click();
  await page.click("#confirm-ok");
  ok("restore: " + await toast(/Restore/));

  // phone width
  await page.setViewportSize({ width: 390, height: 844 });
  await page.click("#nav button[data-view=links]");
  await page.waitForTimeout(500);
  await shot("09-phone-tiles");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  if (overflow) fail("horizontal scroll at 390 px"); else ok("no horizontal scroll at 390 px");

  await page.click("#logout");
  await page.locator("#login").waitFor({ state: "visible" });
  ok("signed out");
  if (problems.length) fail("browser console errors / CSP violations:\n  " + problems.join("\n  "));
  else ok("no console errors or CSP violations");
}

try {
  if (mode === "home") await home();
  else await editor();
} catch (e) {
  fail(e.stack || String(e));
} finally {
  await browser.close();
}
