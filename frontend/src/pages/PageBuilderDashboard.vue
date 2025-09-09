<template>
	<div class="flex h-screen">
		<!-- toolbar -->
		<DashboardSidebar class="z-30"></DashboardSidebar>
		<div class="flex w-full flex-1 flex-col overflow-hidden">
			<div
				class="toolbar sticky top-0 z-10 flex h-12 items-center justify-end border-b-[1px] border-outline-gray-1 bg-surface-white p-2 px-3 py-1"
				ref="toolbar">
				<router-link
					:to="{ name: 'builder', params: { pageId: 'new' } }"
					@click="
						() => {
							posthog.capture('builder_new_page_created');
						}
					">
					<BuilderButton
						variant="solid"
						iconLeft="plus"
						class="bg-surface-gray-7 !text-ink-white hover:bg-surface-gray-6">
						New
					</BuilderButton>
        </router-link>
        <BuilderButton
            variant="subtle"
            iconLeft="download"
            class="ml-2"
            @click="openExportDialog">
            Export
        </BuilderButton>
        <BuilderButton
            variant="subtle"
            iconLeft="upload"
            class="ml-1"
            @click="showImportDialog = true">
            Import
        </BuilderButton>
    </div>
    <!-- Export/Import Dialogs -->
    <Dialog v-model="showExportDialog" :options="{ title: 'Export Pages', size: 'xl' }" style="z-index: 60">
      <template #body-content>
        <div class="flex gap-6 py-2">
          <div class="w-2/3">
            <div class="mb-2 flex items-center justify-between">
              <div class="text-sm text-ink-gray-7">Select pages to export</div>
              <div class="flex gap-2 text-xs">
                <button class="rounded border px-2 py-1" @click="() => { for (const p of (webPages.data||[])) dialogSelected[p.name] = true; }">Select all</button>
                <button class="rounded border px-2 py-1" @click="() => { dialogSelected.value = {}; }">Clear</button>
              </div>
            </div>
            <div class="max-h-[50vh] overflow-auto rounded border">
              <div v-if="!webPages.data || !webPages.data.length" class="p-3 text-sm text-ink-gray-6">Loading pages…</div>
              <div v-else>
                <div v-for="p in webPages.data" :key="p.name" class="flex items-center gap-3 border-b p-2 text-sm">
                  <input type="checkbox" v-model="dialogSelected[p.name]" />
                  <div class="flex flex-col">
                    <span class="font-medium">{{ p.page_title || p.name }}</span>
                    <span class="text-xs text-ink-gray-5">{{ p.route }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="w-1/3">
            <div class="mb-3 text-sm text-ink-gray-7">Options</div>
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" v-model="includeDrafts" /> Include drafts
            </label>
            <label class="mt-2 flex items-center gap-2 text-sm">
              <input type="checkbox" v-model="withAssets" /> Include assets
            </label>
            <div class="mt-4 flex justify-end gap-2">
              <button class="rounded border px-3 py-1 text-sm" @click="showExportDialog = false">Cancel</button>
              <button class="rounded bg-blue-600 px-3 py-1 text-sm text-white" :disabled="exporting" @click="doExport">Export</button>
            </div>
          </div>
        </div>
      </template>
    </Dialog>
    <Dialog v-model="showImportDialog" :options="{ title: 'Import Site Archive', size: 'md' }" style="z-index: 60">
      <template #body-content>
        <div class="flex flex-col gap-4 py-2">
          <div class="flex items-center gap-3">
            <label class="w-24 text-right text-sm">Mode</label>
            <select v-model="importMode" class="w-48 rounded border border-outline-gray-2 p-1 text-sm">
              <option value="upsert">upsert</option>
              <option value="create_only">create_only</option>
              <option value="overwrite">overwrite</option>
            </select>
          </div>
          <div class="flex items-center gap-3">
            <label class="w-24 text-right text-sm">Archive</label>
            <input type="file" accept=".zip" @change="(e:any)=> (importFile = e.target.files?.[0] || null)" class="text-sm" />
          </div>
          <div class="flex justify-end gap-2 pt-2">
            <button class="rounded border px-3 py-1 text-sm" @click="showImportDialog = false">Cancel</button>
            <button class="rounded bg-blue-600 px-3 py-1 text-sm text-white disabled:opacity-50" :disabled="importing || !importFile" @click="doImport">
              {{ importing ? 'Importing…' : 'Import' }}
            </button>
          </div>
        </div>
      </template>
    </Dialog>
			<!-- Sidebar -->
			<!-- Main Content -->
			<div class="flex-1 overflow-auto">
				<section class="m-auto mb-32 flex h-fit w-3/4 max-w-6xl flex-col pt-5">
					<!-- list head -->
					<div class="sticky top-0 z-20 mb-8 flex items-center justify-between bg-surface-white px-3 py-5">
						<h1 class="text-xl font-semibold text-ink-gray-9">
							{{ builderStore.activeFolder || "All Pages" }}
						</h1>
						<div class="flex gap-2">
							<div>
								<BuilderButton
									variant="solid"
									v-show="selectionMode && selectedPages.size"
									@click="showFolderSelectorDialog = true">
									Move To Folder
								</BuilderButton>
							</div>
							<div class="relative flex" v-show="!selectionMode">
								<BuilderInput
									class="w-48"
									type="text"
									placeholder="Filter by title or route"
									v-model="searchFilter"
									autofocus
									@input="
										(value: string) => {
											searchFilter = value;
										}
									">
									<template #prefix>
										<FeatherIcon name="search" class="size-4 text-ink-gray-5"></FeatherIcon>
									</template>
								</BuilderInput>
							</div>
							<div class="max-md:hidden" v-show="!selectionMode">
								<BuilderInput
									type="select"
									class="w-24"
									v-model="typeFilter"
									:options="[
										{ label: 'All', value: '' },
										{ label: 'Draft', value: 'draft' },
										{ label: 'Published', value: 'published' },
										{ label: 'Unpublished', value: 'unpublished' },
									]" />
							</div>
							<div class="max-sm:hidden" v-show="!selectionMode">
								<BuilderInput
									type="select"
									class="w-32"
									v-model="orderBy"
									:options="[
										{ label: 'Sort', value: '', disabled: true },
										{ label: 'Last Created', value: 'creation' },
										{ label: 'Last Modified', value: 'modified' },
										{
											label: 'Alphabetically (A-Z)',
											value: 'alphabetically_a_z',
										},
										{
											label: 'Alphabetically (Z-A)',
											value: 'alphabetically_z_a',
										},
									]" />
							</div>
							<div class="max-md:hidden">
								<OptionToggle
									class="[&>div]:min-w-0"
									:options="[
										{
											label: 'Grid',
											value: 'grid',
											icon: 'grid',
											hideLabel: true,
										},
										{
											label: 'List',
											value: 'list',
											icon: 'list',
											hideLabel: true,
										},
									]"
									v-model="displayType"></OptionToggle>
							</div>
						</div>
					</div>
					<!-- pages -->
					<div>
						<div v-if="!webPages.data?.length && !searchFilter && !typeFilter" class="col-span-full">
							<p class="mt-4 px-3 text-base text-gray-500">
								You don't have any pages yet. Click on the "+ New" button to create a new page.
							</p>
						</div>
						<div v-else-if="!webPages.data?.length" class="col-span-full">
							<p class="mt-4 text-base text-gray-500">No matching pages found.</p>
						</div>
						<!-- grid -->
						<div class="grid-col grid gap-3 auto-fill-[220px]" v-if="displayType === 'grid'">
							<PageCard
								v-for="page in webPages.data"
								:selected="selectedPages.has(page.name)"
								@click.capture="($event) => handleClick($event, page)"
								:key="page.page_name"
								:page="page"
								v-on-click-and-hold="() => enableSelectionMode(page)"></PageCard>
						</div>
						<!-- list -->
						<div v-if="displayType === 'list'">
							<PageListItem
								@click.capture="($event) => handleClick($event, page)"
								v-for="page in webPages.data"
								:selected="selectedPages.has(page.name)"
								:key="page.page_name"
								:page="page"
								v-on-click-and-hold="() => enableSelectionMode(page)"></PageListItem>
						</div>
					</div>
					<BuilderButton
						class="m-auto mt-12 w-fit text-sm"
						@click="loadMore"
						v-show="webPages.hasNextPage"
						variant="subtle"
						size="sm">
						Load More
					</BuilderButton>
				</section>
			</div>
		</div>
		<SelectFolder
			v-model="showFolderSelectorDialog"
			:currentFolder="builderStore.activeFolder"
			@folderSelected="setFolder"></SelectFolder>
	</div>
</template>
<script setup lang="ts">
import OptionToggle from "@/components/Controls/OptionToggle.vue";
import DashboardSidebar from "@/components/DashboardSidebar.vue";
import SelectFolder from "@/components/Modals/SelectFolder.vue";
import PageCard from "@/components/PageCard.vue";
import PageListItem from "@/components/PageListItem.vue";
import { webPages } from "@/data/webPage";
import vOnClickAndHold from "@/directives/vOnClickAndHold";
import useBuilderStore from "@/stores/builderStore";
import { posthog } from "@/telemetry";
import { BuilderPage } from "@/types/Builder/BuilderPage";
import { useDark, useEventListener, useStorage, useToggle, watchDebounced } from "@vueuse/core";
import { createResource } from "frappe-ui";
import { toast } from "vue-sonner";
import Dialog from "@/components/Controls/Dialog.vue";
import { onActivated, Ref, ref, watch } from "vue";

const isDark = useDark({
	attribute: "data-theme",
});
const toggleDark = useToggle(isDark);
const builderStore = useBuilderStore();
const displayType = useStorage("displayType", "grid") as Ref<"grid" | "list">;
const showFolderSelectorDialog = ref(false);

const searchFilter = ref("");
const typeFilter = useStorage("typeFilter", "") as Ref<"" | "draft" | "published" | "unpublished">;
const orderBy = useStorage("orderBy", "creation") as Ref<
	"creation" | "modified" | "alphabetically_a_z" | "alphabetically_z_a"
>;

const orderMap = {
	creation: "creation desc",
	modified: "modified desc",
	alphabetically_a_z: "page_title asc",
	alphabetically_z_a: "page_title desc",
};

const selectedPages = ref(new Set<string>());
const selectionMode = ref(false);
const showExportDialog = ref(false);
const includeDrafts = ref(true);
const withAssets = ref(true);
const exporting = ref(false);
const showImportDialog = ref(false);
const importMode = ref<'upsert' | 'create_only' | 'overwrite'>('upsert');
const importFile = ref<File | null>(null);
const importing = ref(false);
const dialogSelected = ref<Record<string, boolean>>({});

const openExportDialog = async () => {
    dialogSelected.value = {};
    for (const name of selectedPages.value) dialogSelected.value[name] = true;
    if (!webPages.data || !webPages.data.length) {
        await webPages.fetch();
    }
    showExportDialog.value = true;
};

onActivated(() => {
	posthog.capture("builder_dashboard_page_visited");
});

watch(
	() => builderStore.activeFolder,
	() => fetchPages(),
);

// remove selection mode when the escape key is pressed
useEventListener(document, "keydown", (ev) => {
	if (ev.key === "Escape") {
		selectedPages.value.clear();
		selectionMode.value = false;
	}
});

const fetchPages = () => {
	const filters = {
		is_template: 0,
	} as any;
	if (typeFilter.value) {
		if (typeFilter.value === "published") {
			filters["published"] = true;
		} else if (typeFilter.value === "unpublished") {
			filters["published"] = false;
		} else if (typeFilter.value === "draft") {
			filters["draft_blocks"] = ["is", "set"];
		}
	}
	const orFilters = {} as any;
	if (searchFilter.value) {
		orFilters["page_title"] = ["like", `%${searchFilter.value}%`];
		orFilters["route"] = ["like", `%${searchFilter.value}%`];
	}
	if (builderStore.activeFolder) {
		filters["project_folder"] = builderStore.activeFolder;
	}

	webPages.update({
		filters,
		orFilters,
		orderBy: orderMap[orderBy.value],
	});
	webPages.fetch();
};

const loadMore = () => {
	webPages.next();
};

const firstHold = ref(false);

const handleClick = (e: MouseEvent, page: BuilderPage) => {
	if (selectionMode.value) {
		e.preventDefault();
		e.stopPropagation();
		if (firstHold.value) {
			firstHold.value = false;
			return;
		}
		if (e.shiftKey) {
			const pages = webPages.data || [];
			// select all pages between the last selected page and the current page
			const lastSelectedPage = selectedPages.value.size
				? pages.find((p: BuilderPage) => p.name === Array.from(selectedPages.value)[0])
				: null;
			if (lastSelectedPage) {
				const lastSelectedPageIndex = pages.indexOf(lastSelectedPage);
				const currentPageIndex = pages.indexOf(page);
				const start = Math.min(lastSelectedPageIndex, currentPageIndex);
				const end = Math.max(lastSelectedPageIndex, currentPageIndex);
				for (let i = start; i <= end; i++) {
					const p = pages[i];
					selectedPages.value.add(p.name);
				}
			}
		} else if (e.ctrlKey || e.metaKey) {
			togglePageSelection(page);
		} else {
			selectedPages.value.clear();
			togglePageSelection(page);
		}
	} else {
		posthog.capture("builder_page_opened", { page_name: page.page_name });
	}
};

const enableSelectionMode = (page: BuilderPage) => {
	selectionMode.value = true;
	firstHold.value = true;
	togglePageSelection(page);
};

const togglePageSelection = (page: BuilderPage) => {
	if (selectedPages.value.has(page.name)) {
		selectedPages.value.delete(page.name);
	} else {
		selectedPages.value.add(page.name);
	}
	// Disable selection mode if no pages are selected
	if (!selectedPages.value.size) {
		selectionMode.value = false;
	}
};

watchDebounced([searchFilter, typeFilter, orderBy], fetchPages, {
	debounce: 300,
	immediate: true,
});

const setFolder = async (folder: string) => {
	createResource({
		method: "POST",
		url: "builder.api.update_page_folder",
	})
		.submit({
			pages: Array.from(selectedPages.value),
			folder_name: folder,
		})
		.then(() => {
			for (const pageName of selectedPages.value) {
				const page = webPages.data?.find((p: BuilderPage) => p.name === pageName);
				if (page) {
					page.project_folder = folder;
				}
			}
			selectedPages.value.clear();
			selectionMode.value = false;
			showFolderSelectorDialog.value = false;
			builderStore.activeFolder = folder;
		});
};

const showSettingsDialog = ref(false);

// Export selected pages
const doExport = async () => {
    const fromDialog = Object.keys(dialogSelected.value).filter((k) => dialogSelected.value[k]);
    const pages = fromDialog.length ? fromDialog : Array.from(selectedPages.value);
    if (!pages.length) return;
    exporting.value = true;
    try {
        const res = await createResource({ method: 'POST', url: 'builder.api.export_site' }).submit({
            pages,
            include_drafts: includeDrafts.value ? 1 : 0,
            with_assets: withAssets.value ? 1 : 0,
        });
        const url = (res as any)?.url;
        if (url) {
            window.open(url, '_blank');
        }
        showExportDialog.value = false;
    } finally {
        exporting.value = false;
    }
};

// Import archive
async function uploadArchive(file: File): Promise<{ file_url: string; name: string }> {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('is_private', '1');
    const res = await fetch('/api/method/upload_file', {
        method: 'POST',
        body: fd,
        credentials: 'include',
        headers: { 'X-Frappe-CSRF-Token': (window as any).csrf_token || '' },
    });
    const data = await res.json();
    if (!res.ok || (data && data.exc)) {
        throw new Error(data?._server_messages || data?.exception || 'Upload failed');
    }
    return data.message;
}

const doImport = async () => {
    if (!importFile.value) return;
    importing.value = true;
    try {
        const uploaded = await uploadArchive(importFile.value);
        const res = await createResource({ method: 'POST', url: 'builder.api.import_site' }).submit({
            archive: uploaded.file_url,
            mode: importMode.value,
            run_async: 1,
        });
        const jobId = (res as any)?.job_id;
        if (jobId) {
            toast.success('Import started');
            showImportDialog.value = false;
            importFile.value = null;
            // poll job status
            pollImportJob(jobId);
        } else {
            // synchronous fallback
            toast.success('Import completed');
            showImportDialog.value = false;
            importFile.value = null;
            fetchPages();
        }
    } finally {
        importing.value = false;
    }
};

async function pollImportJob(jobId: string) {
    let attempts = 0;
    const maxAttempts = 300; // ~10 minutes @ 2s
    const tick = async () => {
        attempts += 1;
        try {
            const r = await createResource({ method: 'POST', url: 'builder.api.get_import_job_status' }).submit({ job_id: jobId });
            const status = (r as any)?.status || 'unknown';
            if (status === 'finished') {
                toast.success('Import completed, reloading…');
                // reload to ensure all caches/stores/UI reflect imported changes
                setTimeout(() => window.location.reload(), 600);
                return;
            }
            if (status === 'failed' || status === 'stopped') {
                toast.error(`Import ${status}`);
                return;
            }
        } catch (e) {
            // swallow and continue polling a few times in case of transient errors
        }
        if (attempts < maxAttempts) {
            setTimeout(tick, 2000);
        } else {
            toast.warning('Import status unknown, please check later');
        }
    };
    tick();
}
</script>
