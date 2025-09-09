import io
import json
import os
import zipfile

import frappe
from frappe.tests.utils import FrappeTestCase

from builder.site_archive import export_site, import_site
from frappe.utils.file_manager import save_file


class TestSiteArchive(FrappeTestCase):
    def setUp(self):
        # create two files
        self.file1 = save_file("hero.jpg", b"binary-image-1", None, None, is_private=0)
        self.file2 = save_file("bg.png", b"binary-image-2", None, None, is_private=0)

        # create simple page referencing these files
        blocks = [
            {
                "element": "body",
                "children": [
                    {
                        "element": "img",
                        "attributes": {"src": self.file1.file_url},
                    },
                    {
                        "element": "div",
                        "baseStyles": {"backgroundImage": f"url({self.file2.file_url})"},
                    },
                ],
            }
        ]
        self.page = frappe.get_doc(
            {
                "doctype": "Builder Page",
                "page_title": "Export Import Test",
                "published": 1,
                "route": "/export-import-test",
                "blocks": blocks,
                "favicon": self.file1.file_url,
                "meta_image": self.file2.file_url,
            }
        ).insert()

    def tearDown(self):
        # cleanup page and files (ignore if missing)
        for doctype, name in (("Builder Page", getattr(self.page, "name", None)),):
            if name and frappe.db.exists(doctype, name):
                frappe.delete_doc(doctype, name, force=1)
        for f in (getattr(self, "file1", None), getattr(self, "file2", None)):
            if f and frappe.db.exists("File", f.name):
                frappe.delete_doc("File", f.name, force=1, delete_permanently=True)

    def test_export_contains_pages_and_assets(self):
        res = export_site(pages=[self.page.name], include_drafts=True, with_assets=True)
        self.assertTrue(res.get("url"))
        # fetch saved file content
        fdoc = frappe.get_doc("File", {"file_url": res["url"]})
        z = zipfile.ZipFile(io.BytesIO(fdoc.get_content()))
        names = set(z.namelist())
        self.assertIn("manifest.json", names)
        # page json present
        self.assertIn("pages/export-import-test.json", names)
        # both assets present
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        self.assertEqual(manifest.get("counts", {}).get("assets"), 2)
        asset_names = {a["stored_name"] for a in manifest.get("assets", [])}
        for n in asset_names:
            self.assertIn(f"assets/{n}", names)

    def test_import_into_clean_site_and_idempotency(self):
        # export first
        res = export_site(pages=[self.page.name], include_drafts=False, with_assets=True)
        archive_url = res["url"]

        # cleanup originals to simulate clean site import
        original_page_name = self.page.name
        frappe.delete_doc("Builder Page", original_page_name, force=1)
        # keep files for export record but remove to test file creation
        for f in (self.file1, self.file2):
            if frappe.db.exists("File", f.name):
                frappe.delete_doc("File", f.name, force=1, delete_permanently=True)

        # import
        out = import_site(archive=archive_url, mode="upsert")
        self.assertEqual(len(out["summary"]["created"]), 1)
        # files created
        self.assertEqual(len(out.get("file_map", {})), 2)

        # change title and re-export + re-import to test update and dedupe
        page = frappe.get_doc("Builder Page", {"route": "/export-import-test"})
        page.page_title = "Changed Title"
        page.save()
        res2 = export_site(pages=[page.name], include_drafts=False, with_assets=True)
        file_count_before = frappe.db.count("File")
        out2 = import_site(archive=res2["url"], mode="upsert")
        self.assertEqual(len(out2["summary"]["updated"]), 1)
        # no new files created (dedupe)
        self.assertEqual(file_count_before, frappe.db.count("File"))

    def test_import_create_only_skips_existing(self):
        res = export_site(pages=[self.page.name], include_drafts=False, with_assets=True)
        # import once (page exists)
        out = import_site(archive=res["url"], mode="create_only")
        # Should skip since page already exists
        self.assertIn("export-import-test", set(out["summary"]["skipped"]))

    def test_security_rejects_unsafe_zip(self):
        # craft malicious zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("../evil.txt", "oops")
            z.writestr(
                "manifest.json",
                json.dumps({"format": "builder-site-archive", "format_version": 1, "pages": [], "assets": []}),
            )
        f = save_file("bad.zip", buf.getvalue(), None, None, is_private=1)
        with self.assertRaises(Exception):
            import_site(archive=f.file_url, mode="upsert")

    def test_manifest_validation(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps({"format": "wrong", "format_version": 1}))
        f = save_file("bad2.zip", buf.getvalue(), None, None, is_private=1)
        with self.assertRaises(Exception):
            import_site(archive=f.file_url, mode="upsert")
