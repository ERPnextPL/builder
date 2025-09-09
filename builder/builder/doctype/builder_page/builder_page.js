frappe.ui.form.on('Builder Page', {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button('Export Page', async () => {
      try {
      const r = await frappe.call({
          method: 'builder.api.export_site',
          type: 'POST',
          args: { pages: JSON.stringify([frm.doc.name]), include_drafts: 1, with_assets: 1 },
        });
        const url = r.message && r.message.url;
        if (url) {
          frappe.show_alert({ message: `Export ready: <a href="${url}">download</a>`, indicator: 'green' });
        }
      } catch (e) {
        frappe.msgprint({ title: 'Export Failed', message: e.message || e, indicator: 'red' });
      }
    });

    frm.add_custom_button('Import Archive', async () => {
      const d = new frappe.ui.Dialog({
        title: 'Import Site Archive',
        fields: [
          { fieldname: 'mode', label: 'Mode', fieldtype: 'Select', options: ['upsert','create_only','overwrite'], default: 'upsert' },
          { fieldname: 'archive', label: 'Archive (.zip)', fieldtype: 'Attach', reqd: 1 }
        ],
        primary_action_label: 'Import',
        primary_action: async (values) => {
          try {
            const r = await frappe.call({
              method: 'builder.api.import_site',
              type: 'POST',
              args: { archive: values.archive, mode: values.mode },
            });
            d.hide();
            frappe.msgprint({ title: 'Import Summary', message: `<pre>${JSON.stringify(r.message, null, 2)}</pre>` });
            frm.reload_doc();
          } catch (e) {
            frappe.msgprint({ title: 'Import Failed', message: e.message || e, indicator: 'red' });
          }
        }
      });
      d.show();
    });
  }
});
