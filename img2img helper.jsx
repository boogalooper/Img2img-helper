#target photoshop
/*
// BEGIN__HARVEST_EXCEPTION_ZSTRING
<javascriptresource>
<name>img2img helper</name>
<eventid>5f6f57dc-80c8-49b4-9ea9-405d132b7b30</eventid>
<terminology><![CDATA[<< /Version 1
                        /Events <<
                        /5f6f57dc-80c8-49b4-9ea9-405d132b7b30 [(img2img helper) <<
                        /recordSettingsToAction [(recordered settings) /boolean]
                        >>]
                         >>
                      >> ]]></terminology>
</javascriptresource>
// END__HARVEST_EXCEPTION_ZSTRING
*/
var APP = {
    name: "img2img helper",
    uuid: "5f6f57dc-80c8-49b4-9ea9-405d132b7b30",
    settingsFile: "img2img helper.desc",
    tempFolder: "img2img helper",
    generatedLayerName: "generated image",
    dialogEnvKey: "img2imgHelperDialogMode",
    cancelToken: "__IMG2IMG_HELPER_CANCELLED__",
    xmp: {
        namespace: "http://ns.img2img-helper.local/generation/1.0/",
        prefix: "Img2imgHelper:",
        property: "generationSettings"
    },
    cache: {
        schemaVersion: 1,
        comfyAnalysisUuid: "d0d01bf8-4bef-419d-adb0-20a8b56f2161"
    }
},
    VER = "0.1",
    DEBUG_FIRST_LAUNCH_WITH_INTERFACE = true,
    API_FILE = "img2img-api",
    API_HOST = "127.0.0.1",
    API_PORT_SEND = 6370,
    API_PORT_LISTEN = 6371,
    API_PROTOCOL = 1,
    START_TIMEOUT = 12000,
    SHORT_TIMEOUT = 8000,
    STARTUP_PROGRESS_DELAY = 1000,
    TRANSLATE_TIMEOUT = 10 * 60 * 1000,
    ANALYZE_TIMEOUT = 90000,
    GENERATION_PREPARE_SEGMENT = 30,
    GENERATION_RUN_SEGMENT = 70,
    PROGRESS_TASK_RANGE = 40,
    BACKEND_COMFY = "comfy",
    BACKEND_FORGE = "forge",
    TRANSFORM_STRETCH = "stretch",
    TRANSFORM_PROPORTIONAL = "proportional",
    startupStartedAt = (new Date()).getTime(),
    s2t = stringIDToTypeID,
    t2s = typeIDToStringID,
    descriptorCodec = new DescriptorCodec(),
    presets = new Presets(),
    cfg = new Config(),
    api = new BridgeApi(),
    generationProgress = new GenerationProgress(),
    generation = new GenerationRuntime(),
    action = new ActionRuntime(),
    backend = new BackendRuntime(),
    ui = new UI(),
    generationTimings = new Delay(),
    str = new Locale(),
    apl = new AM("application"),
    doc = new AM("document"),
    lr = new AM("layer"),
    layerMetadata = new LayerMetadata(),
    isDirty = false,
    initialState = null,
    startupProgress = null,
    isCancelled = false,
    actionPlaybackMode = false,
    actionUsesRecordedSettings = false,
    globalSettings = null,
    settingsReady = false,
    keyboardState = ScriptUI.environment.keyboardState;
$.localize = true;
if (keyboardState.shiftKey && action.getPlaybackParameterCount() != 1) $.setenv(APP.dialogEnvKey, "true");
if (action.hasInterfaceArgument()) $.setenv(APP.dialogEnvKey, "true");
try { init(); }
catch (e) {
    if (startupProgress) { try { startupProgress.close(); } catch (_) { } startupProgress = null; }
    try {
        if (initialState && app.documents.length) app.activeDocument.activeHistoryState = initialState;
    } catch (_) { }
    if (String(e.message) == APP.cancelToken) {
        api.interrupt(generationProgress.getRequestId());
        isCancelled = true;
    } else {
        var settingsSaveError = action.saveAfterError(),
            errorText = APP.name + "\n\n" + e.message +
                (e.line ? "\n\n" + localize(str.jsxLine) + e.line : "");
        if (settingsSaveError) errorText += "\n\n" + localize(str.errSettingsSaveAfterError) +
            "\n" + settingsSaveError;
        ui.showErrorMessage(errorText, APP.name);
        isCancelled = false;
    }
    $.setenv(APP.dialogEnvKey, "true");
}
isCancelled ? "cancel" : undefined;
function init() {
    if (!app.documents.length) return;
    initialState = app.activeDocument.activeHistoryState;
    if (doc.getProperty("mode").value != "RGBColor") throw new Error(localize(str.errMode));
    var playbackCount = action.getPlaybackParameterCount(),
        forceDialog = action.hasInterfaceArgument();
    actionPlaybackMode = playbackCount > 1;
    if (actionPlaybackMode) {
        var actionSettingsMode = action.getRecordedSettingsMode();
        if (actionSettingsMode === false) {
            cfg.load();
            cfg.recordSettingsToAction = cfg.data.recordSettingsToAction = false;
            actionUsesRecordedSettings = false;
        } else {
            cfg.loadFromAction();
            actionUsesRecordedSettings = true;
            globalSettings = new Config();
            globalSettings.load();
            cfg.copySharedLibrariesFrom(globalSettings);
        }
    } else {
        cfg.load();
        if (playbackCount == 1) $.setenv(APP.dialogEnvKey, "true");
    }
    settingsReady = true;
    cfg.cleanReferenceHistory();
    var environmentMode = DEBUG_FIRST_LAUNCH_WITH_INTERFACE ? null : $.getenv(APP.dialogEnvKey),
        showInterface = DEBUG_FIRST_LAUNCH_WITH_INTERFACE || (actionPlaybackMode
            ? (app.playbackDisplayDialogs == DialogModes.ALL || forceDialog)
            : (forceDialog || environmentMode == null || environmentMode == "true"));
    var selection = { result: false, bounds: null, sourceBounds: null, previousGeneration: null, junk: null, inpaint: false };
    app.activeDocument.suspendHistory(localize(str.historyCheckSelection), "checkSelection(selection)");
    if (!selection.result) return;
    try {
        var apiResponsive = false;
        if (api.isRunning()) {
            try { api.ping(null, 1000); apiResponsive = true; } catch (_) { }
        }
        if (!apiResponsive) {
            startupProgress = ui.createStartupProgress(str.progressStartPython, START_TIMEOUT + ANALYZE_TIMEOUT);
            startupProgress.show();
        }
        api.initialize(startupProgress);
        if (startupProgress) startupProgress.setStage(str.progressHandshake, 22);
        backend.applyStatus(api.handshake(startupProgress));
        backend.normalizeActiveBackend();
        if (!backend.hasAvailable()) throw new Error(localize(str.errNoBackendAvailable));
        var initial = backend.loadInitialData(startupProgress),
            responseSeconds = Math.round((((new Date()).getTime() - startupStartedAt) / 1000) * 100) / 100;
        if (startupProgress) {
            startupProgress.complete(); startupProgress.close(); startupProgress = null;
        }
        if (showInterface) {
            var result = mainDialog(selection, initial, responseSeconds);
            if (!result || result.cancelled) {
                if (result && result.saveSettings) action.saveAcceptedSettings();
                else if (!actionPlaybackMode) cfg.save();
                $.setenv(APP.dialogEnvKey, "true");
                isCancelled = true;
                return;
            }
            action.saveAcceptedSettings();
            $.setenv(APP.dialogEnvKey, "false");
            generation.run(selection, result.schema, result.values);
            return;
        }
        if (!initial.schema) return;
        var silentProfile = backend.schemaProfile(initial.schema),
            silentValues = backend.profileValues(initial.schema, silentProfile);
        if (!actionPlaybackMode) cfg.saveToAction();
        generation.run(selection, initial.schema, silentValues);
    } finally {
        if (startupProgress) { try { startupProgress.close(); } catch (_) { } startupProgress = null; }
    }
}
function runWorkflowAnalysisProgress() { return backend.runWorkflowAnalysisProgress(); }
function workflowAnalysisStage() { return backend.workflowAnalysisStage(); }
function errorMessageText(value) {
    if (value === undefined || value === null) return "";
    if (value.message !== undefined) return String(value.message);
    return String(value);
}
function mainDialog(selection, initial, responseSeconds) {
    var selectionBounds = selection.bounds,
        state = {
            backend: initial.backend || cfg.activeBackend,
            workflows: initial.workflows || [],
            forgePresets: initial.forgePresets || [],
            forgeCatalog: initial.forgeCatalog || {},
            schema: initial.schema || null,
            controls: {}, result: null
        },
        w = new Window("dialog{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:15}"),
        gGlobal = w.add("group{orientation:'row',alignChildren:['left','center'],spacing:0,margins:0}"),
        tWH = gGlobal.add("statictext"),
        gGlobalButtons = gGlobal.add("group{orientation:'row',alignChildren:['right','center'],spacing:0,margins:0}"),
        bLoadMetadata = gGlobalButtons.add("button"),
        bSettings = gGlobalButtons.add("button"),
        gSettingsHost = w.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:0}"),
        gSettings = null,
        gOk = w.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,10,0,0]}"),
        bOk = gOk.add("button", undefined, undefined, { name: "ok" });
    w.text = APP.name + " v" + VER + " — " + responseSeconds + "s";
    gGlobal.preferredSize.width = gGlobal.minimumSize.width = gGlobal.maximumSize.width = ui.contentWidth();
    tWH.alignment = ["fill", "center"];
    tWH.minimumSize.width = 0;
    gGlobalButtons.alignment = ["right", "center"];
    gGlobalButtons.preferredSize.height = gGlobalButtons.minimumSize.height = gGlobalButtons.maximumSize.height = ui.presetButtonSize;
    bLoadMetadata.preferredSize = bLoadMetadata.minimumSize = bLoadMetadata.maximumSize = [ui.loadMetadataButtonWidth, ui.presetButtonSize];
    bSettings.preferredSize = bSettings.minimumSize = bSettings.maximumSize = [ui.presetButtonSize, ui.presetButtonSize];
    updateSelectionSummary();
    bLoadMetadata.text = "LOAD"; bLoadMetadata.helpTip = localize(str.loadLayerMetadata);
    bSettings.text = "⚙"; bSettings.helpTip = localize(str.scriptSettings); bSettings.alignment = ["right", "center"];
    bOk.text = localize(str.generate);
    updateMetadataButton(); showControls();
    var showInitialErrors = state.schema && !state.schema.valid;
    w.onShow = function () {
        activateVisibleDenoiseControl();
        if (showInitialErrors) { showInitialErrors = false; showImportantWorkflowErrors(state.schema); }
    };
    bLoadMetadata.onClick = function () {
        var metadata = layerMetadata.read();
        if (!metadata) { updateMetadataButton(); return; }
        try {
            ui.runWithPaletteProgress(str.progressInitializing, function (progress) {
                if (!loadLayerGenerationSettings(metadata, progress)) throw new Error(localize(str.errLayerMetadata));
            });
            updateMetadataButton();
        } catch (e) { ui.showErrorMessage(e); }
    };
    bSettings.onClick = function () {
        saveCurrentValues();
        var oldData = cloneObject(cfg.data), oldStatus = backend.getStatus(),
            settingsResult = showGlobalSettings(), probePerformed = settingsResult.probePerformed;
        if (probePerformed) state.forgeCatalog = {};
        if (!settingsResult.accepted) { backend.applyStatus(oldStatus); return; }
        if (probePerformed || oldData.backendHost != cfg.backendHost || oldData.forgePort != cfg.forgePort || oldData.forgeSchemasFolder != cfg.forgeSchemasFolder)
            state.forgeCatalog = {};
        try {
            backend.normalizeActiveBackend();
            ui.runWithPaletteProgress(str.progressInitializing, function (progress) {
                backend.applyStatus(api.handshake(progress, null, true));
                backend.normalizeActiveBackend();
                if (!backend.hasAvailable()) throw new Error(localize(str.errNoBackendAvailable));
                loadBackend(cfg.activeBackend, progress, true);
            });
            updateMetadataButton();
        } catch (e) {
            cfg.data = oldData; cfg.bindProperties();
            try { backend.applyStatus(api.handshake(null, cfg)); } catch (_) { }
            backend.normalizeActiveBackend();
            ui.showErrorMessage(e);
        }
    };
    bOk.onClick = function () {
        try {
            saveCurrentValues();
            if (!state.schema) return;
            if (!state.schema.valid) throw new Error(str.errWorkflowInvalid);
            state.result = { cancelled: false, backend: state.backend, schema: state.schema, values: collectValues() };
            w.close(1);
        } catch (e) { ui.showErrorMessage(e); }
    };
    w.onClose = function () {
        if (!state.result) {
            saveCurrentValues(); $.setenv(APP.dialogEnvKey, "true");
            state.result = { cancelled: true, saveSettings: true, forceDialogNextLaunch: true };
        }
        return true;
    };
    w.layout.layout(true); w.preferredSize.width = w.minimumSize.width = w.maximumSize.width = ui.mainWindowWidth;
    ui.enableHoverFocus(w);
    w.center(); w.show(); return state.result;
    function updateSelectionSummary() {
        tWH.text = localize(str.selection) + selectionBounds.width + "x" + selectionBounds.height + " (" + roundTo(selectionBounds.width * selectionBounds.height / 1000000, 2) + " MP)";
    }
    function updateMetadataButton() {
        var hasMetadata = layerMetadata.read() != null,
            loadWidth = hasMetadata ? ui.loadMetadataButtonWidth : 0,
            buttonsWidth = ui.presetButtonSize + loadWidth,
            textWidth = ui.headerTextWidth(hasMetadata);
        bLoadMetadata.visible = bLoadMetadata.enabled = hasMetadata;
        bLoadMetadata.preferredSize = bLoadMetadata.minimumSize = bLoadMetadata.maximumSize = [loadWidth, hasMetadata ? ui.presetButtonSize : 0];
        bSettings.preferredSize = bSettings.minimumSize = bSettings.maximumSize = [ui.presetButtonSize, ui.presetButtonSize];
        gGlobalButtons.preferredSize = gGlobalButtons.minimumSize = gGlobalButtons.maximumSize = [buttonsWidth, ui.presetButtonSize];
        tWH.preferredSize = tWH.minimumSize = tWH.maximumSize = [textWidth, ui.presetButtonSize];
        try { gGlobal.layout.layout(true); } catch (_) { }
    }
    function loadLayerGenerationSettings(metadata, progress) {
        if (!metadata || typeof metadata != "object") return false;
        var backendId = metadata.backend == BACKEND_FORGE ? BACKEND_FORGE : BACKEND_COMFY;
        if (!backend.isAvailable(backendId)) return false;
        loadBackend(backendId, progress, true);
        if (backendId == BACKEND_FORGE) {
            var presetId = metadata.workspace_id || metadata.schema_id || String(metadata.workflow_id || "").replace(/^forge:/, "");
            if (!backend.findForgeSchema(state.forgePresets, presetId)) return false;
            cfg.selectedForgePreset = cfg.data.selectedForgePreset = presetId;
            var loadedForge = backend.loadForgeSchema(presetId, state.forgeCatalog, progress, false);
            state.forgeCatalog = loadedForge.catalog;
            state.schema = loadedForge.schema;
            var forgeProfile = cfg.getForgeProfile(presetId);
            applyMetadataToProfile(metadata, forgeProfile, ["autoResize", "resizePreset", "resize", "manualScale", "sizeMultiple", "imageStitchInputs"]);
            showControls(); return true;
        }
        var workflow = null;
        if (metadata.workflow_id) workflow = backend.findWorkflow(state.workflows, metadata.workflow_id);
        if (!workflow && metadata.relative_path) for (var i = 0; i < state.workflows.length; i++) if (state.workflows[i].relative_path == metadata.relative_path) { workflow = state.workflows[i]; break; }
        if (!workflow) return false;
        cfg.selectedWorkflow = cfg.data.selectedWorkflow = workflow.id;
        var profile = cfg.getProfile(workflow.id), previous = cloneObject(profile.bindingOverrides);
        applyMetadataToProfile(metadata, profile, ["autoResize", "resizePreset", "resize", "manualScale", "sizeMultiple", "bindingOverrides", "referenceFiles"]);
        if (!profile.bindingOverrides || typeof profile.bindingOverrides != "object") profile.bindingOverrides = { input: "", mask: "", references: [], output: "", size: "" };
        if (profile.bindingOverrides.mask === undefined) profile.bindingOverrides.mask = "";
        if (!(profile.bindingOverrides.references instanceof Array)) profile.bindingOverrides.references = [];
        if (!bindingOverridesEqual(previous, profile.bindingOverrides)) profile.schemaCache = null;
        reloadSelectedWorkflow(false, progress); return true;
    }
    function applyMetadataToProfile(metadata, profile, allowed) {
        if (metadata.values && typeof metadata.values == "object") for (var key in metadata.values) if (metadata.values.hasOwnProperty(key)) profile.values[key] = cloneObject(metadata.values[key]);
        if (metadata.profile && typeof metadata.profile == "object") for (var i = 0; i < allowed.length; i++) if (metadata.profile[allowed[i]] !== undefined) profile[allowed[i]] = cloneObject(metadata.profile[allowed[i]]);
    }
    function loadBackend(backendId, progress, refresh) {
        if (!backend.isAvailable(backendId)) throw new Error(localize(str.errBackendUnavailable));
        state.backend = cfg.activeBackend = cfg.data.activeBackend = backendId;
        if (backendId == BACKEND_FORGE) {
            state.forgePresets = refresh || !state.forgePresets.length ? backend.refreshForgeSchemas(progress) : state.forgePresets;
            cfg.selectedForgePreset = cfg.data.selectedForgePreset = backend.chooseForgeSchema(state.forgePresets);
            state.forgeCatalog = state.forgeCatalog || {};
            if (cfg.selectedForgePreset) {
                var loadedForge = backend.loadForgeSchema(cfg.selectedForgePreset, state.forgeCatalog, progress, false);
                state.forgeCatalog = loadedForge.catalog;
                state.schema = loadedForge.schema;
            } else state.schema = null;
        } else {
            if (!backend.comfyFolderReady()) {
                state.workflows = [];
                state.schema = null;
                cfg.selectedWorkflow = cfg.data.selectedWorkflow = "";
            } else {
                state.workflows = refresh || !state.workflows.length ? backend.refreshWorkflows(progress) : state.workflows;
                cfg.selectedWorkflow = cfg.data.selectedWorkflow = backend.chooseWorkflow(state.workflows);
                if (state.workflows.length) reloadSelectedWorkflow(false, progress, true); else state.schema = null;
            }
        }
        showControls();
    }
    function showControls() {
        if (gSettings) { try { gSettings.visible = false; } catch (_) { } try { gSettingsHost.remove(gSettings); } catch (_) { } }
        gSettings = gSettingsHost.add("group{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:0}");
        gSettings.preferredSize.width = gSettings.minimumSize.width = gSettings.maximumSize.width = ui.contentWidth();
        state.controls = {};
        addBackendControl(gSettings);
        if (state.backend == BACKEND_FORGE) addForgePresetControl(gSettings); else addWorkflowControl(gSettings);
        if (state.backend == BACKEND_COMFY && !backend.comfyFolderReady()) {
            fitSelectionBounds(selection, 1); updateSelectionSummary();
            var folderNotice = gSettings.add("statictext", undefined, localize(str.infoMissingWorkflowFolder), { multiline: true });
            folderNotice.preferredSize = [ui.contentWidth(), 55];
            gSettings.enabled = false;
            bLoadMetadata.enabled = false;
            bOk.enabled = false;
            bSettings.enabled = true;
            finalizeMainLayout();
            return;
        }
        if (state.backend == BACKEND_FORGE && !backend.forgeFolderReady()) {
            fitSelectionBounds(selection, 1); updateSelectionSummary();
            var schemaFolderNotice = gSettings.add("statictext", undefined, localize(str.infoMissingForgeSchemaFolder), { multiline: true });
            schemaFolderNotice.preferredSize = [ui.contentWidth(), 55];
            gSettings.enabled = false;
            bLoadMetadata.enabled = false;
            bOk.enabled = false;
            bSettings.enabled = true;
            finalizeMainLayout();
            return;
        }
        if (!state.schema) {
            fitSelectionBounds(selection, 1); updateSelectionSummary();
            var emptyText = state.backend == BACKEND_FORGE ? localize(str.infoEmptyForgePresets) : localize(str.infoEmptyWorkflowFolder) + "\n" + cfg.workflowsFolder;
            gSettings.add("statictext", undefined, emptyText, { multiline: true });
            bOk.enabled = false; finalizeMainLayout(); return;
        }
        var profile = backend.schemaProfile(state.schema);
        fitSelectionBounds(selection, profile.sizeMultiple || cfg.sizeMultiple); updateSelectionSummary();
        var visible = profile.visibleControls;
        if (visible === null || visible === undefined) visible = state.schema.recommended_controls || [];
        var definitions = state.schema.controls || [], map = {}, i;
        for (i = 0; i < definitions.length; i++) map[definitions[i].id] = definitions[i];
        addControlsByPrefix("checkpoint", gSettings, definitions, visible, profile);
        addControlById("modules", gSettings, map, visible, profile);
        addControlsByPrefix("vae", gSettings, definitions, visible, profile);
        addControlsByPrefix("text_encoder", gSettings, definitions, visible, profile);
        addControlById("positive_prompt", gSettings, map, visible, profile);
        addControlsByPrefix("lora", gSettings, definitions, visible, profile);
        addControlById("negative_prompt", gSettings, map, visible, profile);
        addControlById("sampler", gSettings, map, visible, profile);
        addControlById("scheduler", gSettings, map, visible, profile);
        addControlById("steps", gSettings, map, visible, profile);
        addControlById("cfg", gSettings, map, visible, profile);
        addControlById("distilled_cfg_scale", gSettings, map, visible, profile);
        addControlById("shift", gSettings, map, visible, profile);
        addControlById("guidance", gSettings, map, visible, profile);
        addControlById("denoise", gSettings, map, visible, profile);
        addControlById("seed", gSettings, map, visible, profile);
        for (i = 0; i < definitions.length; i++) {
            var def = definitions[i];
            if (isPriorityMainControl(def.id)) continue;
            if (!arrayContains(visible, def.id)) continue;
            addControlDefinition(gSettings, def, profile, ui.contentWidth());
        }
        if (state.backend != BACKEND_FORGE) ui.addImageReferenceControls(gSettings, state.schema, profile);
        ui.addResizeControl(gSettings, selectionBounds, profile, state.schema);
        if (state.backend == BACKEND_FORGE && arrayContains(visible, "image_stitch")) {
            ui.addForgeImageStitchControls(gSettings, state.schema, profile, state.controls, function () {
                saveCurrentValues();
                showControls();
            });
        }
        var baseGenerationEnabled = !!state.schema.valid && !!selection.result;
        bOk.enabled = baseGenerationEnabled;
        if (state.backend == BACKEND_FORGE)
            applyForgeSchemaRules(state.schema, state.controls, baseGenerationEnabled);
        finalizeMainLayout();
    }
    function finalizeMainLayout() {
        ui.enableHoverFocus(gSettings);
        try { gSettings.layout.layout(true); } catch (_) { } try { gSettingsHost.layout.layout(true); } catch (_) { }
        try { w.layout.layout(true); } catch (_) { } try { w.layout.resize(); } catch (_) { } try { if (w.visible) w.update(); } catch (_) { }
        activateVisibleDenoiseControl();
    }
    function activateVisibleDenoiseControl() {
        var item = state.controls ? state.controls.denoise : null,
            control = item ? item.control : null;
        if (!control) return;
        try { if (control.visible === false || control.enabled === false) return; } catch (_) { }
        try { control.active = true; } catch (_) { }
    }
    function showImportantWorkflowErrors(schema) {
        if (!schema || !schema.diagnostics || !schema.diagnostics.length) return;
        var lines = []; for (var i = 0; i < schema.diagnostics.length; i++) if (String(schema.diagnostics[i].level || "").toLowerCase() == "error") lines.push(String(schema.diagnostics[i].message || ""));
        if (lines.length) ui.showErrorMessage(lines.join("\n"));
    }
    function addBackendControl(parent) {
        var items = [];
        if (backend.isAvailable(BACKEND_COMFY)) items.push({ label: "ComfyUI", value: BACKEND_COMFY });
        if (backend.isAvailable(BACKEND_FORGE)) items.push({ label: "Forge Neo", value: BACKEND_FORGE });
        if (items.length < 2) return;
        var control = ui.addDropdown(parent, localize(str.backendLabel), items, ui.contentWidth(), [0, 5, 0, 5]);
        ui.selectDropdown(control.dropdown, state.backend, 0);
        control.dropdown.onChange = function () {
            if (!this.selection) return;
            var selectedBackend = this.selection.controlValue || this.selection.text;
            if (selectedBackend == state.backend) return;
            saveCurrentValues();
            try { ui.runWithPaletteProgress(str.progressInitializing, function (progress) { loadBackend(selectedBackend, progress, true); }); }
            catch (e) { ui.showErrorMessage(e); showControls(); }
        };
    }
    function addWorkflowControl(parent) {
        var group = ui.addColumn(parent, [0, 10, 0, 5]); group.preferredSize.width = group.minimumSize.width = group.maximumSize.width = ui.contentWidth();
        var title = group.add("statictext"), row = group.add("group{orientation:'row',alignChildren:['fill','center'],spacing:0,margins:0}"),
            dropdown = row.add("dropdownlist"), refresh = row.add("button{preferredSize:[25,-1]}"),
            reinit = row.add("button{preferredSize:[25,-1]}"), settings = row.add("button{preferredSize:[25,-1]}");
        row.preferredSize.width = row.minimumSize.width = row.maximumSize.width = ui.contentWidth();
        dropdown.alignment = ["fill", "center"];
        dropdown.preferredSize.width = ui.toolbarFieldWidth(3);
        title.text = localize(str.workflow); refresh.text = "↻"; refresh.helpTip = str.refreshWorkflows; reinit.text = "⟳"; reinit.helpTip = str.reinitializeWorkflow; settings.text = "⚙"; settings.helpTip = str.workflowSettings;
        var selected = 0;
        for (var i = 0; i < state.workflows.length; i++) { var wf = state.workflows[i], item = dropdown.add("item", wf.relative_path || wf.name); item.workflowId = wf.id; if (wf.id == cfg.selectedWorkflow) selected = i; }
        if (dropdown.items.length) dropdown.selection = selected;
        var enabled = state.workflows.length > 0; dropdown.enabled = refresh.enabled = reinit.enabled = settings.enabled = enabled;
        dropdown.onChange = function () { if (!this.selection) return; saveCurrentValues(); cfg.selectedWorkflow = cfg.data.selectedWorkflow = this.selection.workflowId; try { ui.runWithPaletteProgress(str.progressInitializing, function (progress) { reloadSelectedWorkflow(false, progress); }); showImportantWorkflowErrors(state.schema); } catch (e) { ui.showErrorMessage(e); } };
        refresh.onClick = function () { try { saveCurrentValues(); state.workflows = ui.runWithPaletteProgress(str.progressWorkflows, function (progress) { return backend.refreshWorkflows(progress); }); cfg.selectedWorkflow = cfg.data.selectedWorkflow = backend.chooseWorkflow(state.workflows); if (state.workflows.length) ui.runWithPaletteProgress(str.progressInitializing, function (progress) { reloadSelectedWorkflow(false, progress); }); else { state.schema = null; showControls(); } } catch (e) { ui.showErrorMessage(e); } };
        reinit.onClick = function () { try { saveCurrentValues(); ui.runWithPaletteProgress(str.progressAnalyze, function (progress) { reloadSelectedWorkflow(true, progress); }); showImportantWorkflowErrors(state.schema); } catch (e) { ui.showErrorMessage(e); } };
        settings.onClick = function () {
            if (!state.schema) return;
            saveCurrentValues();
            var profile = cfg.getProfile(state.schema.workflow_id),
                previous = cloneObject(profile.bindingOverrides);
            if (!showWorkflowSettings(state.schema, profile)) return;
            try {
                if (!bindingOverridesEqual(previous, profile.bindingOverrides)) {
                    profile.schemaCache = null;
                    ui.runWithPaletteProgress(str.progressAnalyze, function (progress) {
                        reloadSelectedWorkflow(false, progress, true);
                    });
                    showImportantWorkflowErrors(state.schema);
                }
            } catch (e) {
                ui.showErrorMessage(e);
            } finally {
                showControls();
            }
        };
    }
    function addForgePresetControl(parent) {
        var group = ui.addColumn(parent, [0, 10, 0, 5]); group.preferredSize.width = group.minimumSize.width = group.maximumSize.width = ui.contentWidth();
        var title = group.add("statictext"), row = group.add("group{orientation:'row',alignChildren:['fill','center'],spacing:0,margins:0}"),
            dropdown = row.add("dropdownlist"), refresh = row.add("button{preferredSize:[25,-1]}"),
            reinit = row.add("button{preferredSize:[25,-1]}"), settings = row.add("button{preferredSize:[25,-1]}");
        row.preferredSize.width = row.minimumSize.width = row.maximumSize.width = ui.contentWidth();
        dropdown.alignment = ["fill", "center"];
        dropdown.preferredSize.width = ui.toolbarFieldWidth(3);
        title.text = localize(str.uiPreset); refresh.text = "↻"; refresh.helpTip = localize(str.refreshForgeCatalog);
        reinit.text = "⟳"; reinit.helpTip = localize(str.reloadForgeSchemas);
        settings.text = "⚙"; settings.helpTip = localize(str.forgeSchemaSettings);
        var selected = 0;
        for (var i = 0; i < state.forgePresets.length; i++) { var preset = state.forgePresets[i], item = dropdown.add("item", preset.label || preset.id); item.presetId = preset.id; if (preset.id == cfg.selectedForgePreset) selected = i; }
        if (dropdown.items.length) dropdown.selection = selected;
        dropdown.enabled = refresh.enabled = reinit.enabled = settings.enabled = state.forgePresets.length > 0;
        dropdown.onChange = function () {
            if (!this.selection) return;
            saveCurrentValues();
            cfg.selectedForgePreset = cfg.data.selectedForgePreset = this.selection.presetId;
            try {
                ui.runWithPaletteProgress(str.progressInitializing, function (progress) {
                    var loadedForge = backend.loadForgeSchema(cfg.selectedForgePreset, state.forgeCatalog, progress, false);
                    state.forgeCatalog = loadedForge.catalog;
                    state.schema = loadedForge.schema;
                });
                showControls();
            } catch (e) { ui.showErrorMessage(e); }
        };
        refresh.onClick = function () {
            try {
                saveCurrentValues();
                ui.runWithPaletteProgress(str.progressForgeCatalog, function (progress) {
                    var loadedForge = backend.loadForgeSchema(cfg.selectedForgePreset, state.forgeCatalog, progress, true);
                    state.forgeCatalog = loadedForge.catalog;
                    state.schema = loadedForge.schema;
                });
                showControls();
            } catch (e) { ui.showErrorMessage(e); }
        };
        reinit.onClick = function () {
            try {
                saveCurrentValues();
                ui.runWithPaletteProgress(str.progressForgePresets, function (progress) {
                    state.forgePresets = backend.refreshForgeSchemas(progress);
                    cfg.selectedForgePreset = cfg.data.selectedForgePreset = backend.chooseForgeSchema(state.forgePresets);
                    if (cfg.selectedForgePreset) {
                        var loadedForge = backend.loadForgeSchema(cfg.selectedForgePreset, state.forgeCatalog, progress, true);
                        state.forgeCatalog = loadedForge.catalog;
                        state.schema = loadedForge.schema;
                    } else state.schema = null;
                });
                showControls();
            } catch (e) { ui.showErrorMessage(e); }
        };
        settings.onClick = function () {
            if (!state.schema) return;
            saveCurrentValues();
            var profile = cfg.getForgeProfile(state.schema.workspace_id || String(state.schema.workflow_id || "").replace(/^forge:/, ""));
            if (showForgeSchemaSettings(state.schema, profile)) {
                try {
                    ui.runWithPaletteProgress(str.progressForgeCatalog, function (progress) {
                        state.forgeCatalog = backend.ensureForgeCatalog(state.schema, state.forgeCatalog, progress, false);
                        state.schema = backend.hydrateForgeSchema(state.schema, state.forgeCatalog);
                    });
                } catch (e) { ui.showErrorMessage(e); }
                showControls();
            }
        };
    }
    function reloadSelectedWorkflow(force, progress, noShow) {
        isDirty = false; var workflow = backend.findWorkflow(state.workflows, cfg.selectedWorkflow); if (!workflow) throw new Error(str.errSelectedWorkflowMissing);
        var profile = cfg.getProfile(workflow.id); profile.relativePath = workflow.relative_path || profile.relativePath || "";
        var schema = force ? null : cfg.getCachedSchema(workflow.id, workflow);
        if (!schema) { schema = backend.analyzeWorkflow(workflow, profile, force, progress); cfg.cacheSchema(schema, workflow); }
        state.schema = schema; if (!noShow) showControls();
    }
    function addControlById(id, parent, map, visible, profile) { if (map[id] && arrayContains(visible, id)) addControlDefinition(parent, map[id], profile, ui.contentWidth()); }
    function addControlsByPrefix(prefix, parent, definitions, visible, profile) { for (var i = 0; i < definitions.length; i++) if (startsWithSemantic(definitions[i].id, prefix) && arrayContains(visible, definitions[i].id)) addControlDefinition(parent, definitions[i], profile, ui.contentWidth()); }
    function isPriorityMainControl(id) {
        if (startsWithSemantic(id, "checkpoint") || startsWithSemantic(id, "vae") || startsWithSemantic(id, "text_encoder") || startsWithSemantic(id, "lora")) return true;
        return id == "modules" || id == "positive_prompt" || id == "negative_prompt" || id == "sampler" || id == "scheduler" || id == "steps" || id == "cfg" || id == "distilled_cfg_scale" || id == "shift" || id == "guidance" || id == "denoise" || id == "seed";
    }
    function addControlDefinition(parent, definition, profile, preferredWidth) {
        var hasStoredValue = profile.values.hasOwnProperty(definition.id),
            stored = hasStoredValue ? profile.values[definition.id] : cloneObject(definition.value);
        if (definition.type == "multiselect") {
            stored = ui.normalizeMultiselect(definition, stored);
            if (hasStoredValue) profile.values[definition.id] = cloneObject(stored);
        }
        state.controls[definition.id] = ui.addDynamic(parent, definition, stored, preferredWidth, { selectForgeLora: selectForgeLora });
    }
    function saveCurrentValues() {
        if (!state.schema) return;
        var profile = backend.schemaProfile(state.schema), values = collectValues();
        for (var key in values) if (values.hasOwnProperty(key)) profile.values[key] = cloneObject(values[key]);
    }
    function collectValues() {
        var result = {}, key;
        for (key in state.controls) if (state.controls.hasOwnProperty(key)) result[key] = state.controls[key].getValue();
        if (state.schema && backend.schemaBackend(state.schema) == BACKEND_FORGE) {
            var profile = backend.schemaProfile(state.schema), visible = profile.visibleControls;
            if (visible === null || visible === undefined) visible = state.schema.recommended_controls || [];
            var definitions = state.schema.controls || [];
            for (var i = 0; i < definitions.length; i++) {
                var definition = definitions[i];
                if (!result.hasOwnProperty(definition.id)) result[definition.id] = cloneObject(definition.value);
            }
            if (state.schema.capabilities && state.schema.capabilities.image_stitch && !result.hasOwnProperty("image_stitch"))
                result.image_stitch = !!state.schema.image_stitch_default;
        }
        return result;
    }
    function applyForgeSchemaRules(schema, controls, baseEnabled) {
        var generationRules = schema && schema.generation ? schema.generation : {},
            definitions = schema && schema.controls instanceof Array ? schema.controls : [],
            definitionMap = {}, watched = {}, i;
        for (i = 0; i < definitions.length; i++) definitionMap[String(definitions[i].id || "")] = definitions[i];
        function currentValue(id) {
            if (controls[id] && controls[id].getValue) return controls[id].getValue();
            return definitionMap[id] ? cloneObject(definitionMap[id].value) : false;
        }
        function truthy(value) {
            if (typeof value == "string") return /^(1|true|yes|on)$/i.test(value.replace(/^\s+|\s+$/g, ""));
            return !!value;
        }
        function refreshRules() {
            for (var j = 0; j < definitions.length; j++) {
                var definition = definitions[j], dependency = String(definition.enabled_by || ""), target;
                if (!dependency) continue;
                target = controls[String(definition.id || "")];
                if (target && target.container) target.container.enabled = truthy(currentValue(dependency));
            }
            var allowed = true, required = generationRules.require_any;
            if (required instanceof Array && required.length) {
                allowed = false;
                for (var k = 0; k < required.length; k++)
                    if (truthy(currentValue(String(required[k])))) { allowed = true; break; }
            }
            bOk.enabled = baseEnabled && allowed;
        }
        function watch(id) {
            id = String(id || "");
            if (!id || watched[id] || !controls[id] || !controls[id].control) return;
            watched[id] = true;
            controls[id].control.onClick = refreshRules;
        }
        for (i = 0; i < definitions.length; i++) watch(definitions[i].enabled_by);
        if (generationRules.require_any instanceof Array)
            for (i = 0; i < generationRules.require_any.length; i++) watch(generationRules.require_any[i]);
        refreshRules();
    }
    function selectForgeLora(items) {
        if (!(items instanceof Array) || !items.length) return "";
        var w = new Window("dialog{orientation:'column',alignChildren:['fill','top'],spacing:8,margins:15}"),
            search = w.add("edittext"),
            list = w.add("listbox"),
            buttons = w.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,5,0,0]}"),
            ok = buttons.add("button", undefined, "OK", { name: "ok" }),
            selected = "";
        w.text = localize(str.selectLora);
        search.preferredSize = [500, -1];
        search.helpTip = localize(str.loraSearch);
        list.preferredSize = [500, 300];
        ok.enabled = false;
        rebuild("");
        search.onChanging = function () { rebuild(this.text); };
        list.onChange = function () { ok.enabled = !!this.selection; };
        list.onDoubleClick = function () { if (this.selection) { selected = this.selection.loraName || this.selection.text; w.close(1); } };
        ok.onClick = function () { if (!list.selection) return; selected = list.selection.loraName || list.selection.text; w.close(1); };
        ui.enableHoverFocus(w);
        w.center();
        w.show();
        return selected;
        function rebuild(filter) {
            filter = String(filter || "").toLowerCase();
            list.removeAll();
            for (var i = 0; i < items.length; i++) {
                var name = String(items[i] || "");
                if (filter && name.toLowerCase().indexOf(filter) < 0) continue;
                var item = list.add("item", name);
                item.loraName = name;
            }
            if (list.items.length) list.selection = 0;
            ok.enabled = !!list.selection;
        }
    }
    function showWorkflowSettings(schema, profile) {
        var w = new Window("dialog{orientation:'column',alignChildren:['fill','top'],spacing:8,margins:15}");
        w.text = str.workflowSettings;
        var note = w.add("statictext{preferredSize:[540,52],properties:{multiline:true}}");
        note.text = str.workflowTagNote;
        var candidates = schema.candidates || {},
            inputDropdown = addCandidateDropdown(w, str.inputImage, candidates.input || [], profile.bindingOverrides.input, true),
            maskDropdown = addCandidateDropdown(w, str.inpaintMask, candidates.mask || [], profile.bindingOverrides.mask, true);
        var referencePanel = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
        referencePanel.text = str.referenceInputs;
        var referenceList = referencePanel.add("listbox", undefined, [], { multiselect: true });
        referenceList.preferredSize = [520, 95];
        var referenceCandidates = candidates.reference || candidates.input || [],
            selectedReferences = profile.bindingOverrides.references || [];
        for (var referenceIndex = 0; referenceIndex < referenceCandidates.length; referenceIndex++) {
            var referenceCandidate = referenceCandidates[referenceIndex],
                referenceItem = referenceList.add("item", referenceCandidate.label);
            referenceItem.candidateId = referenceCandidate.id;
            referenceItem.selected = arrayContains(selectedReferences, referenceCandidate.id);
        }
        var referenceNote = referencePanel.add("statictext", undefined, str.referenceInputsHelp, { multiline: true });
        referenceNote.preferredSize = [520, 32];
        var outputDropdown = addCandidateDropdown(w, str.outputImage, candidates.output || [], profile.bindingOverrides.output, true),
            sizeDropdown = addCandidateDropdown(w, str.primarySize, candidates.size || [], profile.bindingOverrides.size, true);
        var visiblePanel = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
        visiblePanel.text = str.visibleParameters;
        var list = visiblePanel.add("listbox", undefined, [], { multiselect: true });
        list.preferredSize = [520, 250];
        var visible = profile.visibleControls;
        if (visible === null || visible === undefined) visible = schema.recommended_controls || [];
        var controls = schema.controls || [], i;
        for (i = 0; i < controls.length; i++) {
            var item = list.add("item", ui.label(controls[i]) + "  [" + controls[i].id + "]");
            item.controlId = controls[i].id;
            item.selected = arrayContains(visible, controls[i].id);
        }
        var selectRow = visiblePanel.add("group{orientation:'row',alignChildren:['fill','center'],spacing:5,margins:0}"),
            recommended = selectRow.add("button", undefined, str.recommended);
        var all = selectRow.add("button", undefined, str.all),
            none = selectRow.add("button", undefined, str.none);
        recommended.onClick = function () {
            var ids = schema.recommended_controls || [];
            for (var j = 0; j < list.items.length; j++) list.items[j].selected = arrayContains(ids, list.items[j].controlId);
        };
        all.onClick = function () { for (var j = 0; j < list.items.length; j++) list.items[j].selected = true; };
        none.onClick = function () { for (var j = 0; j < list.items.length; j++) list.items[j].selected = false; };
        var multipleRow = w.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            multipleTitle = multipleRow.add("statictext{preferredSize:[175,-1]}");
        var multiple = multipleRow.add("edittext{preferredSize:[80,-1]}");
        multipleTitle.text = str.sizeMultiple;
        multiple.text = String(profile.sizeMultiple || cfg.sizeMultiple);
        var buttons = w.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,10,0,0]}"),
            ok = buttons.add("button", undefined, str.saveChanges, { name: "ok" });
        var accepted = false;
        ok.onClick = function () {
            profile.bindingOverrides.input = candidateId(inputDropdown);
            profile.bindingOverrides.references = [];
            var mainInputId = profile.bindingOverrides.input;
            for (var referenceItemIndex = 0; referenceItemIndex < referenceList.items.length; referenceItemIndex++) {
                var selectedReferenceItem = referenceList.items[referenceItemIndex];
                if (selectedReferenceItem.selected && selectedReferenceItem.candidateId != mainInputId)
                    profile.bindingOverrides.references.push(selectedReferenceItem.candidateId);
            }
            profile.bindingOverrides.mask = candidateId(maskDropdown);
            profile.bindingOverrides.output = candidateId(outputDropdown);
            profile.bindingOverrides.size = candidateId(sizeDropdown);
            profile.visibleControls = [];
            for (var j = 0; j < list.items.length; j++) if (list.items[j].selected) profile.visibleControls.push(list.items[j].controlId);
            profile.sizeMultiple = clamp(parseInt(multiple.text, 10) || cfg.sizeMultiple, 1, 256);
            accepted = true;
            w.close(1);
        };
        ui.enableHoverFocus(w);
        w.center(); w.show();
        return accepted;
    }
    function showForgeSchemaSettings(schema, profile) {
        var w = new Window("dialog{orientation:'column',alignChildren:['fill','top'],spacing:8,margins:15}");
        w.text = localize(str.forgeSchemaSettings);
        var note = w.add("statictext", undefined, localize(str.forgeSchemaSettingsNote), { multiline: true });
        note.preferredSize = [540, 44];
        var visiblePanel = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
        visiblePanel.text = localize(str.visibleParameters);
        var list = visiblePanel.add("listbox", undefined, [], { multiselect: true });
        list.preferredSize = [520, 270];
        var visible = profile.visibleControls;
        if (visible === null || visible === undefined) visible = schema.recommended_controls || [];
        var controls = forgeEditorControls(schema);
        for (var i = 0; i < controls.length; i++) {
            var definition = controls[i], required = isRequiredForgeControl(definition),
                label = ui.label(definition) + "  [" + definition.id + "]" + (required ? " — " + localize(str.alwaysVisible) : ""),
                item = list.add("item", label);
            item.controlId = definition.id;
            item.requiredVisible = required;
            item.selected = required || arrayContains(visible, definition.id);
            if (required) try { item.enabled = false; } catch (_) { }
        }
        var selectRow = visiblePanel.add("group{orientation:'row',alignChildren:['fill','center'],spacing:5,margins:0}"),
            recommended = selectRow.add("button", undefined, localize(str.recommended)),
            all = selectRow.add("button", undefined, localize(str.all)),
            none = selectRow.add("button", undefined, localize(str.none));
        recommended.onClick = function () {
            var ids = schema.recommended_controls || [];
            for (var j = 0; j < list.items.length; j++)
                list.items[j].selected = list.items[j].requiredVisible || arrayContains(ids, list.items[j].controlId);
        };
        all.onClick = function () { for (var j = 0; j < list.items.length; j++) list.items[j].selected = true; };
        none.onClick = function () { for (var j = 0; j < list.items.length; j++) list.items[j].selected = !!list.items[j].requiredVisible; };
        var multipleRow = w.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            multipleTitle = multipleRow.add("statictext{preferredSize:[175,-1]}"),
            multiple = multipleRow.add("edittext{preferredSize:[80,-1]}");
        multipleTitle.text = localize(str.sizeMultiple);
        multiple.text = String(profile.sizeMultiple || cfg.sizeMultiple);
        var buttons = w.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,10,0,0]}"),
            ok = buttons.add("button", undefined, localize(str.saveChanges), { name: "ok" }), accepted = false;
        ok.onClick = function () {
            profile.visibleControls = [];
            for (var j = 0; j < list.items.length; j++)
                if (list.items[j].selected || list.items[j].requiredVisible) profile.visibleControls.push(list.items[j].controlId);
            profile.sizeMultiple = clamp(parseInt(multiple.text, 10) || cfg.sizeMultiple, 1, 256);
            accepted = true; w.close(1);
        };
        ui.enableHoverFocus(w);
        w.center(); w.show(); return accepted;
    }
    function forgeEditorControls(schema) {
        var result = [], controls = schema && schema.controls instanceof Array ? schema.controls : [];
        for (var i = 0; i < controls.length; i++) result.push(controls[i]);
        if (schema && schema.capabilities && schema.capabilities.image_stitch)
            result.push({ id: "image_stitch", label: "ImageStitch", value: !!schema.image_stitch_default });
        return result;
    }
    function isRequiredForgeControl(definition) {
        if (!definition) return false;
        if (definition.required_visible) return true;
        var id = String(definition.id || "");
        return startsWithSemantic(id, "checkpoint") || id == "modules" || startsWithSemantic(id, "vae") || startsWithSemantic(id, "text_encoder");
    }
    function addCandidateDropdown(parent, labelText, candidates, selectedId, includeAutomatic) {
        var row = parent.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            label = row.add("statictext{preferredSize:[175,-1]}");
        var dropdown = row.add("dropdownlist{preferredSize:[360,-1]}");
        label.text = labelText;
        var selected = 0, offset = 0;
        if (includeAutomatic) {
            var automatic = dropdown.add("item", str.automatic);
            automatic.candidateId = "";
            offset = 1;
        }
        for (var i = 0; i < candidates.length; i++) {
            var item = dropdown.add("item", candidates[i].label);
            item.candidateId = candidates[i].id;
            if (candidates[i].id == selectedId) selected = i + offset;
        }
        if (dropdown.items.length) dropdown.selection = selected;
        return dropdown;
    }
    function candidateId(dropdown) {
        return dropdown && dropdown.selection ? dropdown.selection.candidateId : "";
    }
    function showGlobalSettings() {
        var temp = cloneObject(cfg.data),
            w = new Window("dialog{orientation:'column',alignChildren:['fill','top'],spacing:10,margins:14}"),
            connection = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}"),
            statusRow = connection.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            statusTitle = statusRow.add("statictext{preferredSize:[105,-1]}"), statusValue = statusRow.add("statictext{preferredSize:[240,-1]}"),
            testConnection = statusRow.add("button{preferredSize:[35,25]}"),
            hostRow = connection.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            hostTitle = hostRow.add("statictext{preferredSize:[105,-1]}"), hostEdit = hostRow.add("edittext"),
            comfyPortRow = connection.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            comfyPortTitle = comfyPortRow.add("statictext{preferredSize:[105,-1]}"), comfyPortEdit = comfyPortRow.add("edittext{preferredSize:[70,-1]}"),
            forgePortRow = connection.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            forgePortTitle = forgePortRow.add("statictext{preferredSize:[105,-1]}"), forgePortEdit = forgePortRow.add("edittext{preferredSize:[70,-1]}"),
            folderRow = connection.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            folderTitle = folderRow.add("statictext{preferredSize:[105,-1]}"), folderEdit = folderRow.add("edittext", undefined, "", { readonly: true }), browse = folderRow.add("button{preferredSize:[25,25]}"),
            forgeFolderRow = connection.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            forgeFolderTitle = forgeFolderRow.add("statictext{preferredSize:[105,-1]}"), forgeFolderEdit = forgeFolderRow.add("edittext", undefined, "", { readonly: true }), forgeBrowse = forgeFolderRow.add("button{preferredSize:[25,25]}");
        w.text = localize(str.scriptSettings); connection.text = localize(str.connectionSettings);
        statusRow.preferredSize.width = hostRow.preferredSize.width = comfyPortRow.preferredSize.width = forgePortRow.preferredSize.width = folderRow.preferredSize.width = forgeFolderRow.preferredSize.width = ui.settingsControlWidth;
        statusRow.minimumSize.width = hostRow.minimumSize.width = comfyPortRow.minimumSize.width = forgePortRow.minimumSize.width = folderRow.minimumSize.width = forgeFolderRow.minimumSize.width = ui.settingsControlWidth;
        statusRow.maximumSize.width = hostRow.maximumSize.width = comfyPortRow.maximumSize.width = forgePortRow.maximumSize.width = folderRow.maximumSize.width = forgeFolderRow.maximumSize.width = ui.settingsControlWidth;
        statusTitle.text = localize(str.detectedBackends); statusValue.text = backend.statusLabel(); testConnection.text = "↻"; testConnection.helpTip = localize(str.detectBackends);
        hostTitle.text = localize(str.host); hostEdit.text = temp.backendHost || "127.0.0.1"; hostEdit.preferredSize = [275, -1];
        comfyPortTitle.text = localize(str.comfyPort); comfyPortEdit.text = String(temp.comfyPort || 8188);
        forgePortTitle.text = localize(str.forgePort); forgePortEdit.text = String(temp.forgePort || 7860);
        folderTitle.text = localize(str.workflowFolder); folderEdit.text = temp.workflowsFolder || ""; folderEdit.preferredSize = [240, -1]; browse.text = "...";
        forgeFolderTitle.text = localize(str.forgeSchemaFolder); forgeFolderEdit.text = temp.forgeSchemasFolder || backend.defaultForgeFolder(); forgeFolderEdit.preferredSize = [240, -1]; forgeBrowse.text = "...";
        browse.onClick = function () { var folder = Folder.selectDialog(localize(str.selectWorkflowFolder)); if (folder) folderEdit.text = folder.fsName; };
        forgeBrowse.onClick = function () { var folder = Folder.selectDialog(localize(str.selectForgeSchemaFolder)); if (folder) forgeFolderEdit.text = folder.fsName; };
        function updateBackendFields() {
            var comfyMode = temp.activeBackend != BACKEND_FORGE;
            comfyPortRow.enabled = folderRow.enabled = comfyMode;
            forgePortRow.enabled = forgeFolderRow.enabled = !comfyMode;
        }
        updateBackendFields();
        var probePerformed = false;
        testConnection.onClick = function () {
            var probe = cloneObject(temp);
            probe.backendHost = String(hostEdit.text || "").replace(/^\s+|\s+$/g, "") || "127.0.0.1";
            probe.comfyPort = clamp(parseInt(comfyPortEdit.text, 10) || 8188, 1, 65535);
            probe.forgePort = clamp(parseInt(forgePortEdit.text, 10) || 7860, 1, 65535);
            probe.workflowsFolder = folderEdit.text || "";
            probe.forgeSchemasFolder = forgeFolderEdit.text || "";
            try {
                probePerformed = true;
                ui.runWithPaletteProgress(str.progressHandshake, function (progress) { backend.applyStatus(api.probeBackends(probe, progress)); });
                statusValue.text = backend.statusLabel();
            } catch (e) { ui.showErrorMessage(e); }
        };
        var resize = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
        resize.text = str.resizePresetManagement;
        var resizeEditor = resizePresetEditor(resize, temp);
        var output = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
        output.text = str.imageSettings;
        var flatten = output.add("checkbox"); flatten.text = str.flatten; flatten.value = temp.flatten;
        var rasterize = output.add("checkbox"); rasterize.text = str.rasterize; rasterize.value = temp.rasterizeImage;
        var keepAspectRatio = output.add("checkbox");
        keepAspectRatio.text = str.keepAspectRatioDuringPlace;
        keepAspectRatio.value = temp.keepAspectRatioDuringPlace;
        var brush = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
        brush.text = str.brushSettings;
        var selectBrush = brush.add("checkbox"); selectBrush.text = str.selectBrush; selectBrush.value = temp.selectBrush;
        var opacityControl = ui.addSlider(brush, str.opacity, 1, 100, temp.brushOpacity, { displayValue: temp.brushOpacity, controlWidth: ui.settingsControlWidth });
        opacityControl.slider.onChange = function () { opacityControl.valueText.text = Math.round(this.value); };
        opacityControl.slider.onChanging = function () { this.onChange(); };
        var recordSettings = w.add("checkbox");
        recordSettings.text = localize(str.recordSettingsToAction);
        recordSettings.value = temp.recordSettingsToAction;
        var metadata = w.add("checkbox");
        metadata.text = localize(str.layerMetadata);
        metadata.value = temp.writeLayerMetadata;
        var timeoutRow = w.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
            timeoutTitle = timeoutRow.add("statictext{preferredSize:[220,-1]}");
        var timeout = timeoutRow.add("edittext{preferredSize:[65,-1]}");
        timeoutTitle.text = str.generationTimeout;
        timeout.text = String(temp.generationTimeout);
        function resizePresetEditor(parent, tempCfg) {
            if (!tempCfg.resizePresets || !tempCfg.resizePresets.length) tempCfg.resizePresets = cloneObject(presets.defaultResize());
            var toolbar = ui.addPresetToolbar(parent, ui.settingsControlWidth, str.presetRestore),
                presetList = toolbar.dropdown,
                minControl = presetSlider(parent, {
                    title: str.minimumSide, min: 256, max: 4096, value: 512, step: 64, suffix: " px"
                }),
                maxControl = presetSlider(parent, {
                    title: str.maximumMp, min: 25, max: 1200, value: 200, step: 25, suffix: " MP"
                }),
                minSync = minControl.slider.onChange,
                maxSync = maxControl.slider.onChange;
            minControl.slider.onChange = function () { minSync.call(this); checkIntegrity(); };
            maxControl.slider.onChange = function () { maxSync.call(this); checkIntegrity(); };
            toolbar.refresh.onClick = function () { loadSelection(); };
            toolbar.add.onClick = function () {
                var current = readPreset(),
                    defaultName = presetList.selection ? tempCfg.resizePresets[presetList.selection.index].name + localize(str.presetCopy) : localize(str.resizePresetNew),
                    name = prompt(localize(str.resizePresetPrompt), defaultName, localize(str.resizePresetTitle));
                name = name == null ? "" : String(name).replace(/^\s+|\s+$/g, "");
                if (!name.length) return;
                var found = presets.findResizeIndex(name, tempCfg.resizePresets);
                if (found >= 0) {
                    if (!confirm(localize(str.errResizePreset, name), false, localize(str.resizePresetTitle))) return;
                    tempCfg.resizePresets[found] = presets.createResize(name, current.minSide, current.maxMp);
                } else {
                    tempCfg.resizePresets.push(presets.createResize(name, current.minSide, current.maxMp));
                    found = tempCfg.resizePresets.length - 1;
                }
                refreshList(found);
            };
            toolbar.save.onClick = function () { saveActive(true); };
            toolbar.remove.onClick = function () {
                if (!presetList.selection || presets.isProtectedResize(tempCfg.resizePresets[presetList.selection.index].name)) return;
                tempCfg.resizePresets.splice(presetList.selection.index, 1);
                refreshList(0);
            };
            presetList.onChange = function () { loadSelection(); };
            refreshList(0);
            function refreshList(index) {
                presetList.removeAll();
                for (var i = 0; i < tempCfg.resizePresets.length; i++) presetList.add("item", tempCfg.resizePresets[i].name);
                if (!presetList.items.length) return;
                if (index == null || index < 0) index = 0;
                presetList.selection = Math.min(index, presetList.items.length - 1);
                loadSelection();
            }
            function loadSelection() {
                if (!presetList.selection) { checkIntegrity(); return; }
                var preset = tempCfg.resizePresets[presetList.selection.index];
                minControl.slider.value = preset.minSide;
                maxControl.slider.value = preset.maxMp * 100;
                syncPresetSlider(minControl, 64, false);
                syncPresetSlider(maxControl, 25, true);
                checkIntegrity();
            }
            function checkIntegrity() {
                if (!presetList.selection) {
                    toolbar.refresh.enabled = toolbar.save.enabled = toolbar.remove.enabled = false;
                    return;
                }
                var current = readPreset(),
                    preset = tempCfg.resizePresets[presetList.selection.index],
                    changed = current.minSide != preset.minSide || current.maxMp != preset.maxMp;
                toolbar.refresh.enabled = toolbar.save.enabled = changed;
                toolbar.remove.enabled = tempCfg.resizePresets.length > 1 && !presets.isProtectedResize(preset.name);
            }
            function readPreset() {
                return {
                    minSide: Math.round(minControl.slider.value / 64) * 64,
                    maxMp: Math.round(maxControl.slider.value / 25) * 25 / 100
                };
            }
            function saveActive(refresh) {
                if (!presetList.selection) return false;
                var current = readPreset(), index = presetList.selection.index, preset = tempCfg.resizePresets[index];
                if (current.minSide == preset.minSide && current.maxMp == preset.maxMp) return false;
                tempCfg.resizePresets[index] = presets.createResize(preset.name, current.minSide, current.maxMp);
                if (refresh) refreshList(index); else checkIntegrity();
                return true;
            }
            return { saveActive: function () { return saveActive(false); } };
        }
        function presetSlider(parent, options) {
            var group = parent.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:0}"),
                titleGroup = group.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}");
            group.preferredSize.width = group.minimumSize.width = group.maximumSize.width = ui.settingsControlWidth;
            var label = titleGroup.add('statictext'),
                valueText = titleGroup.add('statictext{justify:"right"}');
            var slider = group.add('slider');
            label.text = options.title;
            label.alignment = ['fill', 'center'];
            valueText.alignment = ['right', 'center'];
            slider.minvalue = options.min;
            slider.maxvalue = options.max;
            slider.value = options.value;
            slider.onChange = function () {
                syncPresetSlider({ slider: slider, value: valueText, suffix: options.suffix }, options.step, options.suffix == ' MP');
            };
            slider.onChanging = function () { slider.onChange(); };
            slider.onChange();
            return { slider: slider, value: valueText, suffix: options.suffix };
        }
        function syncPresetSlider(control, step, decimal) {
            var value = Math.round(control.slider.value / step) * step;
            control.slider.value = value;
            control.value.text = (decimal ? value / 100 : value) + control.suffix;
        }
        var buttons = w.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,10,0,0]}"),
            ok = buttons.add("button", undefined, localize(str.saveChanges), { name: "ok" }), accepted = false;
        ok.onClick = function () {
            var folderChanged = temp.workflowsFolder != folderEdit.text,
                forgeFolderChanged = temp.forgeSchemasFolder != forgeFolderEdit.text;
            temp.backendHost = String(hostEdit.text || "").replace(/^\s+|\s+$/g, "") || "127.0.0.1";
            temp.comfyPort = clamp(parseInt(comfyPortEdit.text, 10) || 8188, 1, 65535);
            temp.forgePort = clamp(parseInt(forgePortEdit.text, 10) || 7860, 1, 65535);
            temp.workflowsFolder = folderEdit.text || "";
            temp.forgeSchemasFolder = forgeFolderEdit.text || "";
            if (resizeEditor && resizeEditor.saveActive) resizeEditor.saveActive();
            temp.flatten = flatten.value; temp.rasterizeImage = rasterize.value; temp.keepAspectRatioDuringPlace = keepAspectRatio.value;
            temp.recordSettingsToAction = recordSettings.value; temp.writeLayerMetadata = metadata.value; temp.selectBrush = selectBrush.value;
            temp.brushOpacity = clamp(Math.round(opacityControl.slider.value), 1, 100); temp.generationTimeout = clamp(parseInt(timeout.text, 10) || 1200, 30, 86400);
            if (folderChanged) { temp.workflowCatalog = []; temp.selectedWorkflow = ""; }
            if (forgeFolderChanged) { temp.forgeCatalog = []; temp.selectedForgePreset = ""; }
            cfg.data = temp; cfg.bindProperties(); accepted = true; w.close(1);
        };
        ui.enableHoverFocus(w);
        w.center(); w.show();
        return { accepted: accepted, probePerformed: probePerformed };
    }
}
function arrayContainsCaseInsensitive(array, value) {
    value = String(value).toUpperCase();
    for (var i = 0; i < array.length; i++) if (String(array[i]).toUpperCase() == value) return true;
    return false;
}
function GenerationRuntime() {
    function isSeedControl(schema) {
        var id = String(schema.id || "").toLowerCase(),
            input = String(schema.input || "").toLowerCase();
        return id == "seed" || id.indexOf("seed__") == 0 || input == "seed" || input == "noise_seed";
    }
    function makeRandomUiSeed(schema) {
        var minimum = parseInt(schema.min, 10),
            maximum = parseInt(schema.max, 10);
        if (isNaN(minimum) || minimum < 0) minimum = 0;
        if (isNaN(maximum) || maximum > 4294967295 || maximum <= minimum) maximum = 4294967295;
        return minimum + Math.floor(Math.random() * (maximum - minimum + 1));
    }
    function collectReferenceFiles(schema, profile) {
        var result = [],
            bindings = schema && schema.bindings ? (schema.bindings.reference_images || []) : [];
        if (!profile.referenceFiles) return result;
        for (var i = 0; i < bindings.length; i++) {
            var path = profile.referenceFiles[bindings[i].id] || "";
            if (path) result.push({ binding_id: bindings[i].id, path: path });
        }
        return result;
    }
    function collectForgeImageInputs(profile) {
        var result = [], values = profile.imageStitchInputs instanceof Array ? profile.imageStitchInputs : [];
        for (var i = 0; i < values.length && result.length < 3; i++) {
            if (!values[i]) continue;
            var file = new File(values[i]);
            if (file.exists && !arrayContainsCaseInsensitive(result, file.fsName)) result.push(file.fsName);
        }
        return result;
    }
    function getProfileTargetSize(bounds, profile) {
        var scale;
        if (profile.autoResize) {
            scale = isDirty
                ? profile.resize
                : autoScale(bounds, presets.findResize(profile.resizePreset, cfg.resizePresets));
        } else {
            scale = profile.manualScale;
        }
        return calculateSizeFromScale(bounds.width, bounds.height, scale || 1, profile.sizeMultiple || cfg.sizeMultiple);
    }
    function run(selection, schema, values) {
        var requestId = createRequestId(),
            inputFile = null,
            maskFile = null,
            resultFile = null;
        try {
            var currentBackend = backend.schemaBackend(schema),
                profile = backend.schemaProfile(schema),
                inpaintMode = getComfyInpaintMode(selection, schema);
            fitSelectionBounds(selection, profile.sizeMultiple || cfg.sizeMultiple);
            var targetSize = getProfileTargetSize(selection.bounds, profile),
                width = targetSize.width,
                height = targetSize.height;
            app.activeDocument.suspendHistory(localize(str.historyPrepareSelection), "prepareSelectionLayer(selection)");
            var exportedFiles = exportSelectionFiles(selection, width, height, requestId, inpaintMode);
            inputFile = exportedFiles.input;
            maskFile = exportedFiles.mask;
            var message;
            if (currentBackend == BACKEND_FORGE) {
                message = {
                    schema_id: schema.workspace_id || String(schema.workflow_id || "").replace(/^forge:/, ""),
                    schema_folder: cfg.forgeSchemasFolder || "",
                    input: inputFile.fsName,
                    width: width,
                    height: height,
                    values: values,
                    image_inputs: collectForgeImageInputs(profile),
                    timeout: cfg.generationTimeout
                };
            } else {
                message = {
                    workflow_id: schema.workflow_id,
                    relative_path: schema.relative_path || profile.relativePath || "",
                    input: inputFile.fsName,
                    mask: maskFile ? maskFile.fsName : "",
                    inpaint_mode: inpaintMode,
                    width: width,
                    height: height,
                    values: values,
                    references: collectReferenceFiles(schema, profile),
                    binding_overrides: profile.bindingOverrides,
                    timeout: cfg.generationTimeout
                };
            }
            var command = {
                protocol: API_PROTOCOL,
                request_id: requestId,
                type: currentBackend == BACKEND_FORGE ? "forge_generate" : "generate",
                message: message
            },
                progressTitles = buildGenerationProgressTitles(currentBackend, schema, values),
                timingKey = currentBackend + ":" + String(schema.workflow_id || schema.workspace_id || schema.relative_path || "default");
            generationProgress.begin({
                command: command,
                titles: progressTitles,
                timingKey: timingKey,
                timingMax: generationTimings.getDelay(timingKey),
                requestId: requestId
            });
            app.doProgress(progressTitles.window, "runGenerationProgress()");
            var progressResult = generationProgress.getResult();
            if (progressResult === false || (progressResult && progressResult.type == "cancelled")) {
                $.setenv(APP.dialogEnvKey, "true");
                throw new Error(APP.cancelToken);
            }
            if (!progressResult) throw new Error(str.errNoResult);
            if (progressResult.type == "error") throw new Error(progressResult.message);
            var answerMessage = progressResult.message,
                resultPath = typeof answerMessage == "object" ? answerMessage.path : answerMessage;
            resultFile = new File(resultPath);
            if (!resultFile.exists) throw new Error(str.errResultFile + "\n" + resultPath);
            layerMetadata.set({
                backend: currentBackend,
                workspace_id: currentBackend == BACKEND_FORGE
                    ? (schema.workspace_id || String(schema.workflow_id || "").replace(/^forge:/, ""))
                    : schema.workflow_id,
                workflow_id: currentBackend == BACKEND_COMFY ? schema.workflow_id : "",
                relative_path: schema.relative_path || profile.relativePath || "",
                workflow_hash: typeof answerMessage == "object" ? answerMessage.workflow_hash || "" : "",
                prompt_id: typeof answerMessage == "object" ? answerMessage.prompt_id || "" : "",
                values: values,
                generated_seeds: typeof answerMessage == "object" ? answerMessage.generated_seeds || {} : {},
                profile: {
                    autoResize: profile.autoResize,
                    resizePreset: profile.resizePreset,
                    resize: profile.resize,
                    manualScale: profile.manualScale,
                    sizeMultiple: profile.sizeMultiple,
                    bindingOverrides: currentBackend == BACKEND_COMFY ? cloneObject(profile.bindingOverrides) : {},
                    referenceFiles: currentBackend == BACKEND_COMFY ? cloneObject(profile.referenceFiles) : {},
                    imageStitchInputs: currentBackend == BACKEND_FORGE ? cloneObject(profile.imageStitchInputs) : []
                },
                width: width,
                height: height
            });
            app.activeDocument.suspendHistory(localize(str.historyPlaceResult), "placeResultHistory()");
            function placeResultHistory() { generatedImageToLayer(resultFile, selection); }
            advanceVisibleSeeds(schema, profile, values);
            action.saveAfterGeneration();
            if (typeof answerMessage == "object" && answerMessage.warnings instanceof Array && answerMessage.warnings.length)
                alert(localize(str.generationWarnings) + "\n\n• " + answerMessage.warnings.join("\n• "), APP.name, false);
        } finally {
            if (inputFile && inputFile.exists) try { inputFile.remove(); } catch (_) { }
            if (maskFile && maskFile.exists) try { maskFile.remove(); } catch (_) { }
            if (resultFile && resultFile.exists) try { resultFile.remove(); } catch (_) { }
            generationProgress.clear();
        }
    }
    function getComfyInpaintMode(selection, schema) {
        if (!selection.inpaint || backend.schemaBackend(schema) != BACKEND_COMFY) return "";
        var binding = schema && schema.bindings ? schema.bindings.inpaint_mask : null;
        if (!binding || !binding.mode) throw new Error(localize(str.errInpaintMaskMissing));
        if (!binding.connected) {
            if (binding.mode == "input_alpha") throw new Error(localize(str.errInpaintInputDisconnected));
            throw new Error(localize(str.errInpaintNodeDisconnected));
        }
        return binding.mode;
    }
    function buildGenerationProgressTitles(backendId, schema, values) {
        var backendLabel = backendId == BACKEND_FORGE ? "Forge" : "Comfy",
            checkpoint = findGenerationCheckpointName(schema, values),
            fallback = generationWorkspaceName(schema, backendId),
            subject = sanitizeProgressSubject(checkpoint || fallback);
        if (!subject) subject = backendId == BACKEND_FORGE ? "schema" : "workspace";
        return {
            window: backendLabel + ": " + localize(str.generationProgressTitle),
            prepare: backendLabel + ": " + localize(str.progressInitializeAction) + " " + subject + "… ",
            generate: backendLabel + ": " + localize(str.progressGenerateAction) + "… "
        };
    }
    function findGenerationCheckpointName(schema, values) {
        var controls = schema && schema.controls instanceof Array ? schema.controls : [],
            source = values || {};
        for (var i = 0; i < controls.length; i++) {
            var definition = controls[i], id = String(definition.id || "");
            if (!startsWithSemantic(id, "checkpoint")) continue;
            var value = source.hasOwnProperty(id) ? source[id] : definition.value;
            value = firstProgressValue(value);
            if (value) return value;
        }
        return "";
    }
    function firstProgressValue(value) {
        if (value instanceof Array) {
            for (var i = 0; i < value.length; i++) {
                var item = firstProgressValue(value[i]);
                if (item) return item;
            }
            return "";
        }
        if (value && typeof value == "object") {
            if (value.value !== undefined) return firstProgressValue(value.value);
            if (value.label !== undefined) return firstProgressValue(value.label);
            return "";
        }
        var text = String(value === undefined || value === null ? "" : value).replace(/^\s+|\s+$/g, "");
        if (!text || /^(none|null|undefined)$/i.test(text)) return "";
        return text;
    }
    function generationWorkspaceName(schema, backendId) {
        if (!schema) return "";
        if (backendId == BACKEND_FORGE)
            return schema.label || schema.workspace_id || String(schema.workflow_id || "").replace(/^forge:/, "");
        return schema.workflow_name || schema.label || schema.relative_path || schema.workflow_id || "";
    }
    function sanitizeProgressSubject(value) {
        var text = firstProgressValue(value).replace(/\\/g, "/"), parts = text.split("/");
        text = parts.length ? parts[parts.length - 1] : text;
        return text.replace(/\./g, "-").replace(/\s+/g, " ").replace(/^\s+|\s+$/g, "");
    }
    function advanceVisibleSeeds(schema, profile, sentValues) {
        if (!schema || !profile || !sentValues) return;
        var controls = schema.controls || [];
        for (var i = 0; i < controls.length; i++) {
            var definition = controls[i];
            if (!isSeedControl(definition)) continue;
            if (!sentValues.hasOwnProperty(definition.id)) continue;
            var previous = String(sentValues[definition.id]),
                next = previous;
            for (var attempt = 0; attempt < 5 && next == previous; attempt++) {
                next = String(makeRandomUiSeed(definition));
            }
            profile.values[definition.id] = next;
        }
    }
    function prepareSelectionLayer(selection) {
        if (selection.previousGeneration) doc.hideSelectedLayers();
        if (doc.getProperty("quickMask")) {
            doc.quickMask("clearEvent");
            doc.makeLayer(APP.generatedLayerName);
            doc.makeSelectionMask();
        } else if (doc.hasProperty("selection")) {
            doc.makeLayer(APP.generatedLayerName);
            doc.makeSelectionMask();
        } else if (isGeneratedLayerName(lr.getProperty("name"))) {
            if (lr.getProperty("hasUserMask")) {
                lr.selectChannel("mask");
                doc.makeSelectionFromLayer("targetEnum");
            } else {
                doc.makeSelectionFromLayer("transparencyEnum");
                doc.makeSelectionMask();
            }
        }
        selection.junk = lr.getProperty("layerID");
        doc.makeSelection(selection.bounds);
        if (cfg.flatten) {
            doc.hideSelectedLayers();
            doc.makeLayer(APP.generatedLayerName);
            doc.mergeVisible();
            doc.selectLayersByIDs([selection.junk]);
        }
    }
    function exportSelectionFiles(selection, width, height, requestId, inpaintMode) {
        var hst = activeDocument.activeHistoryState,
            c = null;
        try { c = doc.getProperty("center").value; } catch (_) { }
        var p = new Folder(Folder.temp.fsName + "/" + APP.tempFolder);
        if (!p.exists) p.create();
        var inputFile = new File(p.fsName + "/IMG2IMG_" + requestId + (inpaintMode == "input_alpha" ? ".png" : ".jpg")),
            maskFile = inpaintMode == "load_image_mask" ? new File(p.fsName + "/INPAINT_MASK_" + requestId + ".png") : null;
        try {
            if (inpaintMode) {
                doc.selectLayersByIDs([selection.junk]);
                lr.selectChannel("mask");
                if (inpaintMode == "load_image_mask") {
                    doc.selectAllPixels();
                    doc.copyPixels();
                } else {
                    doc.invert();
                }
            }
            hideLayersAboveSource(selection.junk);
            doc.makeSelection(selection.bounds);
            doc.crop(true);
            if (inpaintMode == "input_alpha") {
                doc.makeSelectionFromLayer("mask", selection.junk);
                doc.makeLayer(APP.generatedLayerName);
                doc.mergeVisible();
                resizeDocument(width, height);
                doc.makeSelectionMask();
                doc.saveAPNGCopy(inputFile);
            } else {
                doc.flatten();
                if (inpaintMode == "load_image_mask") doc.pastePixels();
                resizeDocument(width, height);
                if (maskFile) {
                    doc.saveAPNGCopy(maskFile);
                    doc.deleteLayer();
                }
                doc.saveACopy(inputFile);
            }
        } finally {
            activeDocument.activeHistoryState = hst;
            if (c) try { doc.setProperty("center", c); } catch (_) { }
        }
        if (!inputFile.exists) throw new Error(inpaintMode == "input_alpha" ? str.errSavePng : str.errSaveJpeg);
        if (maskFile && !maskFile.exists) throw new Error(str.errSaveMask);
        return { input: inputFile, mask: maskFile };
        function resizeDocument(targetWidth, targetHeight) {
            var resolution = Number(doc.getProperty("resolution")) || 72,
                currentWidth = Math.round(Number(doc.getProperty("width")) * resolution / 72),
                currentHeight = Math.round(Number(doc.getProperty("height")) * resolution / 72);
            if (currentWidth != targetWidth || currentHeight != targetHeight) doc.imageSize(targetWidth, targetHeight);
        }
        function hideLayersAboveSource(layerId) {
            var length = doc.getProperty("numberOfLayers"),
                from = lr.getProperty("itemIndex", false, layerId) + (doc.getProperty("hasBackgroundLayer") ? 0 : 1);
            var ids = [];
            for (var i = from; i <= length; i++) {
                var section = lr.getProperty("layerSection", false, i, true);
                if (section && section.value == "layerSectionContent") ids.push(lr.getProperty("layerID", false, i, true));
            }
            if (from <= length && ids.length) {
                doc.selectLayersByIDs(ids);
                doc.hideSelectedLayers();
            }
        }
    }
    function generatedImageToLayer(resultFile, selection) {
        doc.place(resultFile);
        var placed = doc.descToObject(lr.getProperty("bounds").value),
            target = selection.bounds;
        var placedWidth = placed.right - placed.left, placedHeight = placed.bottom - placed.top;
        if (!placedWidth || !placedHeight) throw new Error(str.errPlacedBounds);
        var scaleX = (target.right - target.left) / placedWidth,
            scaleY = (target.bottom - target.top) / placedHeight;
        var transformMode = cfg.keepAspectRatioDuringPlace
            ? TRANSFORM_PROPORTIONAL
            : TRANSFORM_STRETCH;
        if (transformMode == TRANSFORM_PROPORTIONAL) {
            var proportionalScale = Math.min(scaleX, scaleY);
            lr.transform(proportionalScale * 100, proportionalScale * 100);
        } else {
            lr.transform(scaleX * 100, scaleY * 100);
        }
        if (cfg.rasterizeImage) try { lr.rasterize(); } catch (_) { }
        lr.setName(APP.generatedLayerName);
        if (cfg.writeLayerMetadata) layerMetadata.write();
        try { doc.makeSelectionFromLayer("mask", selection.junk); }
        catch (_) { doc.makeSelection(target); }
        if (!doc.hasProperty("selection")) doc.makeSelection(target);
        doc.makeSelectionMask();
        doc.deleteLayer(selection.junk);
        lr.selectChannel("mask");
        if (cfg.selectBrush) {
            try {
                doc.resetSwatches(); doc.selectBrush(); doc.setBrushOpacity(cfg.brushOpacity);
            } catch (_) { }
        }
    }
    function isGeneratedLayerName(name) { return String(name) == APP.generatedLayerName; }
    function checkSelection(result) {
        if (!apl.getProperty("numberOfDocuments")) return;
        if (doc.getProperty("quickMask")) {
            var savedSelection = null;
            if (doc.hasProperty("selection")) savedSelection = doc.descToObject(doc.getProperty("selection").value);
            doc.quickMask("clearEvent");
            if (doc.hasProperty("selection")) {
                result.result = true;
                result.inpaint = true;
                result.bounds = savedSelection || doc.descToObject(doc.getProperty("selection").value);
            }
            doc.quickMask("set");
            if (savedSelection) doc.makeSelection(savedSelection);
            if (result.result) fitSelectionBounds(result, 1);
            return;
        }
        if (doc.hasProperty("selection")) {
            result.result = true;
            result.bounds = doc.descToObject(doc.getProperty("selection").value);
            fitSelectionBounds(result, 1);
            return;
        }
        if (isGeneratedLayerName(lr.getProperty("name"))) {
            doc.makeSelectionFromLayer("transparencyEnum");
            if (doc.hasProperty("selection")) {
                result.result = true;
                result.bounds = doc.descToObject(doc.getProperty("selection").value);
                result.previousGeneration = lr.getProperty("layerID");
            }
            doc.deselect();
            if (result.result) fitSelectionBounds(result, 1);
        }
    }
    this.run = run;
    this.isSeedControl = isSeedControl;
    this.makeRandomSeed = makeRandomUiSeed;
    this.prepareSelectionLayer = prepareSelectionLayer;
    this.checkSelection = checkSelection;
}
function ActionRuntime() {
    function saveSharedLibraries() {
        if (!globalSettings) return;
        cfg.copySharedLibrariesTo(globalSettings);
        globalSettings.save();
    }
    this.getPlaybackParameterCount = function () {
        if (DEBUG_FIRST_LAUNCH_WITH_INTERFACE) return 0;
        try { return app.playbackParameters ? app.playbackParameters.count : 0; }
        catch (_) { return 0; }
    };
    this.hasInterfaceArgument = function () {
        var values = [];
        try {
            if ($.arguments && $.arguments.length) for (var i = 0; i < $.arguments.length; i++) values.push($.arguments[i]);
        } catch (_) { }
        for (var j = 0; j < values.length; j++) {
            var value = String(values[j]).toLowerCase();
            if (value == "dialog" || value == "ui" || value == "--dialog" || value == "--ui" || value == "/dialog" || value == "/ui") return true;
        }
        return false;
    };
    this.getRecordedSettingsMode = function () {
        try {
            var descriptor = app.playbackParameters,
                key = s2t("recordSettingsToAction");
            if (descriptor && descriptor.hasKey(key) && descriptor.getType(key) == DescValueType.BOOLEANTYPE) return descriptor.getBoolean(key);
        } catch (_) { }
        return true;
    };
    this.saveAcceptedSettings = function () {
        if (actionPlaybackMode && actionUsesRecordedSettings && cfg.recordSettingsToAction) {
            cfg.saveToAction();
            saveSharedLibraries();
            return;
        }
        cfg.save();
        cfg.saveToAction();
    };
    this.saveAfterGeneration = function () {
        if (actionPlaybackMode && cfg.recordSettingsToAction) cfg.saveToAction();
        else cfg.save();
    };
    this.saveAfterError = function () {
        if (!settingsReady) return "";
        try {
            this.saveAcceptedSettings();
            return "";
        } catch (saveError) {
            return errorMessageText(saveError) +
                (saveError && saveError.line ? " (" + localize(str.jsxLine) + saveError.line + ")" : "");
        }
    };
}
function BackendRuntime() {
    var status = { mode: "none", available_backends: [], backends: { comfy: { available: false }, forge: { available: false } } };
    function applyStatus(response) {
        if (!response || typeof response != "object") return;
        var source = response.backends ? response : { mode: "none", available_backends: [], backends: {} };
        status = {
            mode: source.mode || "none",
            available_backends: source.available_backends instanceof Array ? source.available_backends : [],
            backends: {
                comfy: source.backends && source.backends.comfy ? source.backends.comfy : { available: false },
                forge: source.backends && source.backends.forge ? source.backends.forge : { available: false }
            }
        };
    }
    function isAvailable(name) {
        return !!(status && status.backends && status.backends[name] && status.backends[name].available);
    }
    function statusLabel(value) {
        value = value || status;
        if (!value || value.mode == "none") return localize(str.backendsNone);
        if (value.mode == "both") return "ComfyUI + Forge Neo";
        return value.mode == BACKEND_COMFY ? "ComfyUI" : "Forge Neo";
    }
    function normalizeActiveBackend() {
        if (isAvailable(cfg.activeBackend)) return;
        if (isAvailable(BACKEND_COMFY)) cfg.activeBackend = cfg.data.activeBackend = BACKEND_COMFY;
        else if (isAvailable(BACKEND_FORGE)) cfg.activeBackend = cfg.data.activeBackend = BACKEND_FORGE;
    }
    function comfyFolderReady() {
        return !!cfg.workflowsFolder && (new Folder(cfg.workflowsFolder)).exists;
    }
    function folderContainsForgeSchema(path) {
        if (!path) return false;
        var folder = new Folder(path);
        if (!folder.exists) return false;
        var files;
        try { files = folder.getFiles("*.json"); } catch (_) { return false; }
        for (var i = 0; i < files.length; i++) {
            if (!(files[i] instanceof File)) continue;
            var opened = false;
            try {
                files[i].encoding = "UTF-8";
                opened = files[i].open("r");
                if (!opened) continue;
                var source = files[i].read(4096);
                files[i].close(); opened = false;
                if (source.indexOf("photoshop-helper-forge-schema") >= 0 && source.indexOf('"backend"') >= 0 && source.indexOf('"forge"') >= 0) return true;
            } catch (_) {
                if (opened) try { files[i].close(); } catch (_) { }
            }
        }
        return false;
    }
    function defaultForgeFolder() {
        var scriptFolder = (new File($.fileName)).parent,
            candidates = [
                new Folder(scriptFolder + "/forge-schemas"),
                new Folder(scriptFolder + "/lib/forge-schemas"),
                scriptFolder,
                new Folder(scriptFolder + "/lib")
            ];
        for (var i = 0; i < candidates.length; i++)
            if (folderContainsForgeSchema(candidates[i].fsName)) return candidates[i].fsName;
        return "";
    }
    function ensureForgeFolder(promptUser) {
        var folderPath = cfg.forgeSchemasFolder || "";
        if (!folderContainsForgeSchema(folderPath)) folderPath = defaultForgeFolder();
        if (!folderPath && promptUser) {
            var selected = Folder.selectDialog(localize(str.selectForgeSchemaFolder));
            if (selected) folderPath = selected.fsName;
        }
        if (!folderPath) return false;
        cfg.forgeSchemasFolder = cfg.data.forgeSchemasFolder = folderPath;
        return true;
    }
    function refreshWorkflows(progress) {
        var response = api.workflowList(progress),
            items = response.items || [];
        cfg.setWorkflowCatalog(items);
        return items;
    }
    function chooseItem(items, selectedId) {
        return findItem(items, selectedId) ? selectedId : (items.length ? items[0].id : "");
    }
    function findItem(items, itemId) {
        for (var i = 0; i < items.length; i++) if (items[i].id == itemId) return items[i];
        return null;
    }
    function chooseWorkflow(workflows) { return chooseItem(workflows, cfg.selectedWorkflow); }
    function findWorkflow(workflows, workflowId) { return findItem(workflows, workflowId); }
    function refreshForgeSchemas(progress) {
        if (!ensureForgeFolder(true)) {
            cfg.forgeCatalog = cfg.data.forgeCatalog = [];
            return [];
        }
        var response = api.forgeSchemaList(progress), items = response.items || [];
        if (response.folder) cfg.forgeSchemasFolder = cfg.data.forgeSchemasFolder = String(response.folder);
        cfg.forgeCatalog = cfg.data.forgeCatalog = items;
        return items;
    }
    function chooseForgeSchema(items) { return chooseItem(items, cfg.selectedForgePreset); }
    function findForgeSchema(items, presetId) { return findItem(items, presetId); }
    function hydrateForgeSchema(schema, catalog) {
        schema = cloneObject(schema || {});
        schema.backend = BACKEND_FORGE;
        catalog = catalog || {};
        var current = catalog.current || {}, controls = schema.controls || [];
        for (var i = 0; i < controls.length; i++) {
            var control = controls[i], source = control.source;
            if (source && catalog[source] instanceof Array) control.items = cloneObject(catalog[source]);
            control.backend = BACKEND_FORGE;
            control.forgeLoras = cloneObject(catalog.loras instanceof Array ? catalog.loras : []);
            if (control.id == "checkpoint" && !control.value) control.value = current.checkpoint || "";
            if (control.id == "modules" && (!(control.value instanceof Array) || !control.value.length)) control.value = cloneObject(current.modules || []);
        }
        return schema;
    }
    function mergeCatalog(base, update) {
        var result = cloneObject(base || {}), key;
        update = update || {};
        for (key in update) if (update.hasOwnProperty(key)) result[key] = cloneObject(update[key]);
        return result;
    }
    function requiredForgeSources(schema) {
        if (!schema) return [];
        var profile = schemaProfile(schema),
            visible = profile.visibleControls,
            controls = schema.controls instanceof Array ? schema.controls : [],
            result = [], seen = {};
        if (visible === null || visible === undefined) visible = schema.recommended_controls || [];
        function add(source) {
            source = String(source || "");
            if (!source || seen[source]) return;
            seen[source] = true;
            result.push(source);
        }
        for (var i = 0; i < controls.length; i++) {
            var control = controls[i], id = String(control.id || ""),
                shown = !!control.required_visible || arrayContains(visible, id);
            if (!shown) continue;
            if (control.source) add(control.source);
            if (id == "positive_prompt") add("loras");
        }
        return result;
    }
    function ensureForgeCatalog(schema, catalog, progress, force) {
        catalog = catalog || {};
        var required = requiredForgeSources(schema), requested = [];
        for (var i = 0; i < required.length; i++)
            if (force || !(catalog[required[i]] instanceof Array)) requested.push(required[i]);
        if (!requested.length) return catalog;
        return mergeCatalog(catalog, api.forgeCatalog(requested, !!force, progress));
    }
    function loadForgeSchema(schemaId, catalog, progress, forceCatalog) {
        var raw = api.forgeSchemaGet(schemaId, progress),
            nextCatalog = ensureForgeCatalog(raw, catalog || {}, progress, !!forceCatalog);
        return { catalog: nextCatalog, schema: hydrateForgeSchema(raw, nextCatalog) };
    }
    var workflowAnalysisArgs = null,
        workflowAnalysisResult = null;
    function schemaBackend(schema) {
        return schema && schema.backend == BACKEND_FORGE ? BACKEND_FORGE : BACKEND_COMFY;
    }
    function schemaProfile(schema) {
        if (schemaBackend(schema) == BACKEND_FORGE)
            return cfg.getForgeProfile(schema.workspace_id || String(schema.workflow_id || "").replace(/^forge:/, ""));
        return cfg.getProfile(schema.workflow_id);
    }
    function profileValues(schema, profile) {
        var result = {},
            visible = profile.visibleControls,
            currentBackend = schemaBackend(schema);
        if (visible === null || visible === undefined) visible = schema.recommended_controls || [];
        var controls = schema.controls || [];
        for (var i = 0; i < controls.length; i++) {
            var definition = controls[i],
                isVisible = arrayContains(visible, definition.id);
            if (currentBackend != BACKEND_FORGE && !isVisible) continue;
            var sourceValue = isVisible && profile.values.hasOwnProperty(definition.id)
                ? cloneObject(profile.values[definition.id])
                : cloneObject(definition.value);
            if (definition.type == "multiselect") {
                sourceValue = ui.normalizeMultiselect(definition, sourceValue);
                if (isVisible && profile.values.hasOwnProperty(definition.id))
                    profile.values[definition.id] = cloneObject(sourceValue);
            }
            result[definition.id] = sourceValue;
        }
        if (currentBackend == BACKEND_FORGE && schema.capabilities && schema.capabilities.image_stitch) {
            var stitchVisible = arrayContains(visible, "image_stitch");
            result.image_stitch = stitchVisible && profile.values.hasOwnProperty("image_stitch")
                ? !!profile.values.image_stitch
                : !!schema.image_stitch_default;
        }
        return result;
    }
    function analyzeWorkflow(workflow, profile, force, progress) {
        if (progress) {
            progress.setStage(str.progressAnalyze, 63);
            var direct = force
                ? api.workflowReinitialize(workflow.id, profile.bindingOverrides, workflow.relative_path, progress)
                : api.workflowGet(workflow.id, profile.bindingOverrides, workflow.relative_path, progress);
            if (!direct) throw new Error(str.errEmptyApiAnswer);
            return direct;
        }
        workflowAnalysisArgs = { workflow: workflow, profile: profile, force: !!force };
        workflowAnalysisResult = null;
        app.doProgress(localize(str.progressAnalyze), "runWorkflowAnalysisProgress()");
        var result = workflowAnalysisResult;
        workflowAnalysisArgs = null;
        workflowAnalysisResult = null;
        if (!result) throw new Error(str.errEmptyApiAnswer);
        return result;
    }
    function runWorkflowAnalysisProgress() {
        if (!app.doProgressSegmentTask(100, 0, 100, "workflowAnalysisStage()")) throw new Error(APP.cancelToken);
        return true;
    }
    function workflowAnalysisStage() {
        var args = workflowAnalysisArgs;
        workflowAnalysisResult = args.force
            ? api.workflowReinitialize(args.workflow.id, args.profile.bindingOverrides, args.workflow.relative_path)
            : api.workflowGet(args.workflow.id, args.profile.bindingOverrides, args.workflow.relative_path);
        return true;
    }
    this.applyStatus = applyStatus;
    this.getStatus = function () { return cloneObject(status); };
    this.hasAvailable = function () { return status.mode != "none"; };
    this.isAvailable = isAvailable;
    this.statusLabel = statusLabel;
    this.normalizeActiveBackend = normalizeActiveBackend;
    this.comfyFolderReady = comfyFolderReady;
    this.defaultForgeFolder = defaultForgeFolder;
    this.forgeFolderReady = function () { return ensureForgeFolder(false); };
    this.refreshWorkflows = refreshWorkflows;
    this.chooseWorkflow = chooseWorkflow;
    this.findWorkflow = findWorkflow;
    this.refreshForgeSchemas = refreshForgeSchemas;
    this.chooseForgeSchema = chooseForgeSchema;
    this.findForgeSchema = findForgeSchema;
    this.hydrateForgeSchema = hydrateForgeSchema;
    this.ensureForgeCatalog = ensureForgeCatalog;
    this.loadForgeSchema = loadForgeSchema;
    this.schemaBackend = schemaBackend;
    this.schemaProfile = schemaProfile;
    this.profileValues = profileValues;
    this.analyzeWorkflow = analyzeWorkflow;
    this.runWorkflowAnalysisProgress = runWorkflowAnalysisProgress;
    this.workflowAnalysisStage = workflowAnalysisStage;
    this.loadInitialData = function (progress) {
        var result = { backend: cfg.activeBackend, workflows: [], forgePresets: [], forgeCatalog: null, schema: null };
        if (cfg.activeBackend == BACKEND_FORGE) {
            if (progress) progress.setStage(str.progressForgePresets, 42);
            result.forgePresets = refreshForgeSchemas(progress);
            cfg.selectedForgePreset = cfg.data.selectedForgePreset = chooseForgeSchema(result.forgePresets);
            result.forgeCatalog = {};
            if (cfg.selectedForgePreset) {
                var loadedForge = loadForgeSchema(cfg.selectedForgePreset, result.forgeCatalog, progress, false);
                result.forgeCatalog = loadedForge.catalog;
                result.schema = loadedForge.schema;
            }
            return result;
        }
        if (!comfyFolderReady()) {
            cfg.selectedWorkflow = cfg.data.selectedWorkflow = "";
            return result;
        }
        if (progress) progress.setStage(str.progressWorkflows, 42);
        result.workflows = refreshWorkflows(progress);
        if (result.workflows.length) {
            cfg.selectedWorkflow = cfg.data.selectedWorkflow = chooseWorkflow(result.workflows);
            var selected = findWorkflow(result.workflows, cfg.selectedWorkflow);
            if (!selected) throw new Error(localize(str.errSelectedWorkflowMissing));
            var profile = cfg.getProfile(selected.id);
            profile.relativePath = selected.relative_path || profile.relativePath || "";
            result.schema = cfg.getCachedSchema(selected.id, selected);
            if (!result.schema) {
                if (!startupProgress) startupProgress = ui.createDelayedStartupProgress(str.progressAnalyze, ANALYZE_TIMEOUT, STARTUP_PROGRESS_DELAY);
                if (progress) progress.setStage(str.progressAnalyze, 63);
                result.schema = analyzeWorkflow(selected, profile, false, progress);
                cfg.cacheSchema(result.schema, selected);
            }
        } else cfg.selectedWorkflow = cfg.data.selectedWorkflow = "";
        return result;
    };
}
function UI() {
    var self = this;
    this.mainWindowWidth = 360;
    this.settingsControlWidth = 385;
    this.presetButtonSize = 25;
    this.loadMetadataButtonWidth = this.presetButtonSize * 2;
    this.sliderValueWidth = 65;
    this.autoResizeCheckboxWidth = 15;
    function describe(source) {
        var objectItem = source && typeof source == "object",
            label = objectItem ? (source.label !== undefined ? source.label : source.value) : source,
            value = objectItem && source.value !== undefined ? source.value : label;
        return { label: String(label === undefined ? "" : label), value: value === undefined ? "" : value };
    }
    function itemValue(item) {
        return item && item.controlValue !== undefined ? item.controlValue : (item ? item.text : "");
    }
    function populate(control, items) {
        items = items instanceof Array ? items : [];
        for (var i = 0; i < items.length; i++) {
            var data = describe(items[i]), item = control.add("item", data.label);
            item.controlValue = data.value;
        }
        return control;
    }
    function read(control, multiselect) {
        if (!multiselect) return control.selection ? itemValue(control.selection) : "";
        var result = [], selection = control.selection;
        if (!selection) return result;
        if (!(selection instanceof Array)) selection = [selection];
        for (var i = 0; i < selection.length; i++) result.push(itemValue(selection[i]));
        return result;
    }
    function restore(control, savedValue, multiselect, fallbackIndex) {
        if (multiselect) {
            var selectedValues = savedValue instanceof Array ? savedValue : [];
            for (var i = 0; i < control.items.length; i++) control.items[i].selected = arrayContains(selectedValues, itemValue(control.items[i]));
            return read(control, true);
        }
        var selected = null;
        for (var j = 0; j < control.items.length; j++) {
            if (String(itemValue(control.items[j])) == String(savedValue)) { selected = control.items[j]; break; }
        }
        if (!selected && fallbackIndex !== undefined && control.items.length) {
            var index = Math.max(0, Math.min(control.items.length - 1, Number(fallbackIndex) || 0));
            selected = control.items[index];
        }
        control.selection = selected || (control.items.length ? control.items[0] : null);
        return control.selection;
    }
    this.contentWidth = function () {
        return Math.max(220, self.mainWindowWidth - 30);
    };
    this.toolbarFieldWidth = function (buttonCount) {
        return Math.max(100, self.contentWidth() - Math.max(0, buttonCount || 0) * self.presetButtonSize);
    };
    this.headerTextWidth = function (hasMetadata) {
        return Math.max(100, self.contentWidth() - self.presetButtonSize - (hasMetadata ? self.loadMetadataButtonWidth : 0));
    };
    this.promptHeight = function () {
        return Math.max(54, 80 - Math.round(Math.max(0, self.mainWindowWidth - 315) * 0.4));
    };
    this.enableHoverFocus = function (root) {
        if (!root) return;
        function attach(control) {
            if (!control) return;
            var type = "", attached = false;
            try { type = String(control.type || "").toLowerCase(); } catch (_) { }
            try { attached = !!control.__comfyForgeHoverFocus; } catch (_) { }
            if (!attached && type == "slider" && control.addEventListener) {
                var activate = function () {
                    try { if (control.visible === false || control.enabled === false) return; } catch (_) { }
                    try { if (!control.active) control.active = true; } catch (_) { }
                };
                try { control.addEventListener("mouseover", activate); } catch (_) { }
                try { control.addEventListener("mousemove", activate); } catch (_) { }
                try { control.__comfyForgeHoverFocus = true; } catch (_) { }
            }
            var children = null;
            try { children = control.children; } catch (_) { children = null; }
            if (!children) return;
            for (var i = 0; i < children.length; i++) attach(children[i]);
        }
        attach(root);
    };
    this.addColumn = function (parent, margins, alignment) {
        margins = margins === undefined ? 0 : margins;
        alignment = alignment || "center";
        var marginsText = margins instanceof Array ? "[" + margins.join(",") + "]" : margins;
        return parent.add("group{orientation:'column',alignChildren:['fill','" + alignment + "'],spacing:0,margins:" + marginsText + "}");
    };
    this.addDropdown = function (parent, labelText, items, preferredWidth, margins) {
        var group = self.addColumn(parent, margins || 0),
            controlWidth = preferredWidth || self.contentWidth();
        group.preferredSize.width = group.minimumSize.width = group.maximumSize.width = controlWidth;
        var title = group.add("statictext"), dropdown = group.add("dropdownlist{preferredSize:[" + controlWidth + ",-1]}");
        dropdown.minimumSize.width = dropdown.maximumSize.width = controlWidth;
        title.text = labelText;
        populate(dropdown, items || []);
        return { group: group, title: title, dropdown: dropdown };
    };
    this.selectDropdown = function (dropdown, value, fallback) { return restore(dropdown, value, false, fallback); };
    this.addSlider = function (parent, labelText, minimum, maximum, value, options) {
        options = options || {};
        var controlWidth = options.controlWidth || self.contentWidth(),
            valueWidth = options.valueWidth || self.sliderValueWidth,
            titleSpacing = options.titleSpacing === undefined ? 0 : options.titleSpacing,
            titleWidth = options.titleWidth || (controlWidth - valueWidth - titleSpacing),
            group = parent.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:" + (options.margins || 0) + "}");
        group.preferredSize.width = group.minimumSize.width = group.maximumSize.width = controlWidth;
        var titleGroup = group.add("group{orientation:'row',alignChildren:['left','center'],spacing:" + titleSpacing + ",margins:0}");
        titleGroup.preferredSize.width = titleGroup.minimumSize.width = titleGroup.maximumSize.width = controlWidth;
        var title = titleGroup.add("statictext{preferredSize:[" + titleWidth + ",-1]}"),
            valueText = titleGroup.add("statictext{preferredSize:[" + valueWidth + ",-1],justify:'right'}"),
            slider = group.add("slider{minvalue:" + minimum + ",maxvalue:" + maximum + "}");
        slider.preferredSize.width = slider.minimumSize.width = slider.maximumSize.width = controlWidth;
        title.text = labelText;
        slider.value = value;
        valueText.text = options.displayValue !== undefined ? options.displayValue : value;
        return { group: group, titleGroup: titleGroup, title: title, valueText: valueText, slider: slider };
    };
    this.normalizeMultiselect = function (schema, storedValue) {
        var items = schema && schema.items instanceof Array ? schema.items : [],
            savedValues = storedValue instanceof Array ? storedValue : [], result = [];
        for (var i = 0; i < items.length; i++) {
            var value = describe(items[i]).value;
            for (var j = 0; j < savedValues.length; j++) {
                if (String(savedValues[j]) == String(value)) {
                    if (!arrayContains(result, value)) result.push(value);
                    break;
                }
            }
        }
        return result;
    };
    function label(schema) {
        var labels = {
            positive_prompt: str.prompt,
            negative_prompt: str.negativePrompt,
            sampler: str.sampler,
            scheduler: str.scheduler,
            steps: str.steps,
            cfg: str.cfgScale,
            guidance: str.guidance,
            denoise: str.denoisingStrength,
            seed: str.seed,
            modules: str.modules,
            distilled_cfg_scale: str.distilledCfgScale,
            shift: str.shift,
            lora: str.lora
        }, id = String(schema.id || ""), semantic = id.split("__")[0], value = labels[semantic];
        if (value === undefined) return schema.label;
        value = localize(value);
        if (id != semantic && schema.label && String(schema.label).indexOf("—") >= 0)
            return value + " " + String(schema.label).substring(String(schema.label).indexOf("—"));
        return value;
    }
    function help(schema) {
        if (schema.help) return localize(schema.help);
        if (schema.node_id !== undefined && schema.input !== undefined) return localize(str.nodeInput) + schema.node_id + ", " + schema.input;
        return schema.label || schema.id || "";
    }
    function addMultiSelect(parent, schema, storedValue, preferredWidth) {
        var group = self.addColumn(parent, 0, "top"),
            title = group.add("statictext"),
            list = group.add("listbox", undefined, [], { multiselect: true });
        group.preferredSize.width = group.minimumSize.width = group.maximumSize.width = preferredWidth;
        title.text = label(schema);
        title.helpTip = help(schema);
        list.preferredSize = [preferredWidth, parseInt(schema.height, 10) || 82];
        populate(list, schema.items || []);
        restore(list, self.normalizeMultiselect(schema, storedValue), true);
        return {
            getValue: function () { return read(list, true); },
            control: list,
            container: group
        };
    }
    this.label = label;
    this.help = help;
    this.addDynamic = function (parent, schema, storedValue, preferredWidth, handlers) {
        if (schema.id == "positive_prompt" || schema.id == "negative_prompt") return addPromptControl(parent, schema, storedValue, handlers);
        if (schema.type == "dropdown") {
            var dropdownControl = self.addDropdown(parent, label(schema), schema.items || [], preferredWidth || self.contentWidth());
            dropdownControl.title.helpTip = help(schema);
            self.selectDropdown(dropdownControl.dropdown, storedValue, 0);
            return {
                getValue: function () { return read(dropdownControl.dropdown, false); },
                control: dropdownControl.dropdown,
                container: dropdownControl.group
            };
        }
        if (schema.type == "multiselect") return addMultiSelect(parent, schema, storedValue, preferredWidth || self.contentWidth());
        if (schema.type == "checkbox") {
            var checkbox = parent.add("checkbox");
            checkbox.text = label(schema);
            checkbox.helpTip = help(schema);
            checkbox.value = !!storedValue;
            return { getValue: function () { return checkbox.value; }, control: checkbox, container: checkbox };
        }
        if (schema.type == "integer" || schema.type == "float") return addNumericControl(parent, schema, storedValue);
        var group = self.addColumn(parent, 0, "top"), title = group.add("statictext");
        title.text = label(schema);
        title.helpTip = help(schema);
        var properties = schema.type == "multiline" ? { multiline: true, scrolling: true } : {},
            edit = group.add("edittext", undefined, String(storedValue === undefined ? "" : storedValue), properties);
        edit.preferredSize = [preferredWidth || self.contentWidth(), schema.type == "multiline" ? 70 : -1];
        return { getValue: function () { return edit.text; }, control: edit, container: group };
    };
    this.addPresetToolbar = function (parent, totalWidth, refreshHelp) {
        totalWidth = Math.max(self.presetButtonSize * 4, Number(totalWidth) || self.contentWidth());
        var buttonBlockWidth = self.presetButtonSize * 4,
            dropdownWidth = Math.max(100, totalWidth - buttonBlockWidth),
            row = parent.add("group{orientation:'row',alignChildren:['left','center'],spacing:0,margins:0}"),
            dropdown = row.add("dropdownlist"),
            buttons = row.add("group{orientation:'row',alignChildren:['left','center'],spacing:0,margins:0}"),
            refresh = buttons.add("button"),
            add = buttons.add("button"),
            save = buttons.add("button"),
            remove = buttons.add("button"),
            controls = [refresh, add, save, remove],
            symbols = [str.presetRefreshButton, str.presetAddButton, str.presetSaveButton, str.presetDeleteButton],
            tips = [refreshHelp || str.presetRestore, str.presetAdd, str.presetSave, str.presetDelete];
        row.preferredSize = row.minimumSize = row.maximumSize = [totalWidth, self.presetButtonSize];
        dropdown.alignment = ["left", "center"];
        dropdown.preferredSize.width = dropdown.minimumSize.width = dropdown.maximumSize.width = dropdownWidth;
        buttons.alignment = ["right", "center"];
        buttons.preferredSize = buttons.minimumSize = buttons.maximumSize = [buttonBlockWidth, self.presetButtonSize];
        for (var i = 0; i < controls.length; i++) {
            controls[i].text = symbols[i];
            controls[i].helpTip = localize(tips[i]);
            controls[i].preferredSize = controls[i].minimumSize = controls[i].maximumSize = [self.presetButtonSize, self.presetButtonSize];
        }
        return { row: row, dropdown: dropdown, refresh: refresh, add: add, save: save, remove: remove };
    };
    function addNumericControl(parent, schema, storedValue) {
        var integer = schema.type == "integer",
            value = parseFloat(storedValue);
        if (isNaN(value)) value = parseFloat(schema.value) || 0;
        if (generation.isSeedControl(schema)) {
            var seedGroup = self.addColumn(parent, 0);
            seedGroup.preferredSize.width = seedGroup.minimumSize.width = seedGroup.maximumSize.width = self.contentWidth();
            var seedTitle = seedGroup.add("statictext"),
                seedRow = seedGroup.add("group{orientation:'row',alignChildren:['fill','center'],spacing:0,margins:0}");
            seedRow.preferredSize.width = seedRow.minimumSize.width = seedRow.maximumSize.width = self.contentWidth();
            var seedEdit = seedRow.add("edittext"),
                seedRefresh = seedRow.add("button{preferredSize:[30,-1]}");
            seedTitle.text = self.label(schema);
            seedTitle.helpTip = self.help(schema);
            seedEdit.alignment = ["fill", "center"];
            seedEdit.preferredSize.width = Math.max(100, self.contentWidth() - 30);
            seedEdit.text = String(storedValue === undefined ? value : storedValue);
            seedEdit.onChanging = function () { filterNumericEditText(this, true); };
            seedRefresh.text = "↻";
            seedRefresh.helpTip = str.randomSeed;
            seedRefresh.onClick = function () { seedEdit.text = String(generation.makeRandomSeed(schema)); };
            return { getValue: function () { return seedEdit.text; }, control: seedEdit, container: seedGroup };
        }
        var hasMinimum = hasNumericSchemaValue(schema.min),
            hasMaximum = hasNumericSchemaValue(schema.max),
            preferredSlider = isPreferredSliderControl(schema),
            minimum = hasMinimum ? parseFloat(schema.min) : Math.min(0, value),
            maximum = hasMaximum ? parseFloat(schema.max) : Math.max(100, value * 2, 1),
            stepsControl = isStepsControl(schema);
        if ((!hasMinimum || !hasMaximum) && !preferredSlider) {
            return addNumericEditControl(parent, schema, storedValue, integer);
        }
        if (isNaN(minimum)) minimum = 0;
        if (isNaN(maximum) || maximum <= minimum) {
            if (!preferredSlider) return addNumericEditControl(parent, schema, storedValue, integer);
            maximum = minimum + 100;
        }
        if (stepsControl) {
            maximum = Math.min(maximum, 100);
            if (maximum <= minimum) minimum = Math.min(minimum, 99);
        }
        var step = numericControlStep(schema, integer),
            precision = integer ? 0 : numberPrecision(step);
        if (Math.abs(maximum - minimum) > 10000000) {
            return addNumericEditControl(parent, schema, storedValue, integer);
        }
        value = clamp(value, minimum, maximum);
        var scale = Math.pow(10, precision),
            sliderMinimum = Math.round(minimum * scale),
            sliderMaximum = Math.round(maximum * scale),
            sliderStep = Math.max(1, Math.round(step * scale)),
            sliderValue = clamp(
                Math.round(roundByStep(value * scale, sliderStep, sliderMinimum)),
                sliderMinimum,
                sliderMaximum
            );
        value = roundTo(sliderValue / scale, precision);
        var sliderControl = self.addSlider(
            parent,
            self.label(schema),
            sliderMinimum,
            sliderMaximum,
            sliderValue,
            { displayValue: formatNumber(value, integer, precision) }
        );
        sliderControl.title.helpTip = self.help(schema);
        sliderControl.slider.onChange = function () {
            var sliderPosition = roundByStep(this.value, sliderStep, sliderMinimum),
                current;
            sliderPosition = clamp(Math.round(sliderPosition), sliderMinimum, sliderMaximum);
            this.value = sliderPosition;
            current = roundTo(sliderPosition / scale, precision);
            sliderControl.valueText.text = formatNumber(current, integer, precision);
        };
        sliderControl.slider.onChanging = function () { this.onChange(); };
        return {
            getValue: function () {
                sliderControl.slider.onChange();
                return integer ? parseInt(sliderControl.valueText.text, 10) : parseFloat(sliderControl.valueText.text);
            },
            control: sliderControl.slider, container: sliderControl.group
        };
    }
    function addNumericEditControl(parent, schema, storedValue, integer) {
        var editGroup = self.addColumn(parent, 0);
        editGroup.preferredSize.width = editGroup.minimumSize.width = editGroup.maximumSize.width = self.contentWidth();
        var title = editGroup.add("statictext"),
            edit = editGroup.add("edittext{preferredSize:[" + self.contentWidth() + ",-1]}");
        title.text = self.label(schema);
        title.helpTip = self.help(schema);
        edit.text = String(storedValue === undefined ? schema.value : storedValue);
        edit.onChanging = function () { filterNumericEditText(this, integer); };
        return {
            getValue: function () {
                var text = normalizeNumericEditText(edit.text, integer),
                    fallback = parseFloat(schema.value),
                    value;
                if (text == "" || text == "-" || text == "." || text == "-.") {
                    value = isNaN(fallback) ? 0 : fallback;
                } else {
                    value = integer ? parseInt(text, 10) : parseFloat(text);
                    if (isNaN(value)) value = isNaN(fallback) ? 0 : fallback;
                }
                var hasMinimum = hasNumericSchemaValue(schema.min),
                    hasMaximum = hasNumericSchemaValue(schema.max),
                    minimum = hasMinimum ? parseFloat(schema.min) : null,
                    maximum = hasMaximum ? parseFloat(schema.max) : null,
                    step = numericControlStep(schema, integer),
                    origin = hasMinimum && !isNaN(minimum) ? minimum : 0;
                if (hasMinimum && !isNaN(minimum)) value = Math.max(minimum, value);
                if (hasMaximum && !isNaN(maximum)) value = Math.min(maximum, value);
                value = roundByStep(value, step, origin);
                if (hasMinimum && !isNaN(minimum)) value = Math.max(minimum, value);
                if (hasMaximum && !isNaN(maximum)) value = Math.min(maximum, value);
                if (integer) {
                    value = Math.round(value);
                    if (Math.abs(value) > 9007199254740991) return text;
                    edit.text = String(value);
                    return String(value);
                }
                var precision = numberPrecision(step);
                value = roundTo(value, precision);
                edit.text = formatNumber(value, false, precision);
                return value;
            },
            control: edit, container: editGroup
        };
    }
    function filterNumericEditText(edit, integer) {
        var normalized = normalizeNumericEditText(edit.text, integer);
        if (edit.text != normalized) edit.text = normalized;
    }
    function normalizeNumericEditText(value, integer) {
        var text = String(value === undefined || value === null ? "" : value).replace(/,/g, "."),
            negative = text.charAt(0) == "-";
        text = text.replace(/-/g, "");
        if (integer) {
            text = text.replace(/[^0-9]/g, "");
        } else {
            text = text.replace(/[^0-9.]/g, "");
            var dot = text.indexOf(".");
            if (dot >= 0) text = text.substring(0, dot + 1) + text.substring(dot + 1).replace(/\./g, "");
        }
        return (negative ? "-" : "") + text;
    }
    function hasNumericSchemaValue(value) {
        return value !== undefined && value !== null && String(value) != "" && !isNaN(parseFloat(value));
    }
    function isStepsControl(schema) {
        return startsWithSemantic(String(schema.id || ""), "steps") || String(schema.input || "").toLowerCase() == "steps";
    }
    function isCoarseHalfStepControl(schema) {
        var id = String(schema.id || ""),
            input = String(schema.input || "").toLowerCase();
        if (startsWithSemantic(id, "cfg") || startsWithSemantic(id, "guidance")) return true;
        return input == "cfg" || input == "cfg_scale" || input == "guidance" ||
            input == "guidance_scale" || input == "flux_guidance" ||
            input == "distilled_cfg" || input == "distilled_cfg_scale";
    }
    function numericControlStep(schema, integer) {
        var step = schema.step !== undefined ? parseFloat(schema.step) : (integer ? 1 : 0.01);
        if (isNaN(step) || step <= 0) step = integer ? 1 : 0.01;
        if (!integer && isCoarseHalfStepControl(schema)) step = 0.5;
        return step;
    }
    function isPreferredSliderControl(schema) {
        var prefixes = [
            "steps", "cfg", "guidance", "denoise",
            "model_strength", "clip_strength", "conditioning_strength",
            "start_percent", "end_percent", "mask_grow", "mask_blur",
            "detection_threshold", "blend", "variation_strength",
            "noise_strength", "tile_overlap"
        ];
        var id = String(schema.id || "");
        for (var i = 0; i < prefixes.length; i++) if (startsWithSemantic(id, prefixes[i])) return true;
        return false;
    }
    function normalizeForgeLoraList(items) {
        var result = [];
        if (!(items instanceof Array)) return result;
        for (var i = 0; i < items.length; i++) {
            var item = items[i], name = "";
            if (typeof item == "string") name = item;
            else if (item && typeof item == "object")
                name = item.name || item.alias || item.value || item.label || item.model_name || item.title || item.filename || item.path || "";
            name = String(name || "").replace(/^\s+|\s+$/g, "");
            if (name && !arrayContainsCaseInsensitive(result, name)) result.push(name);
        }
        result.sort(function (a, b) {
            a = String(a).toLowerCase(); b = String(b).toLowerCase();
            return a == b ? 0 : (a > b ? 1 : -1);
        });
        return result;
    }
    function addPromptControl(parent, schema, storedValue, handlers) {
        var group = parent.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:0}");
        group.preferredSize.width = group.minimumSize.width = group.maximumSize.width = self.contentWidth();
        var title = group.add("statictext"),
            toolbar = self.addPresetToolbar(group, self.contentWidth(), str.promptClear),
            presetList = toolbar.dropdown,
            refresh = toolbar.refresh,
            add = toolbar.add,
            save = toolbar.save,
            remove = toolbar.remove,
            edit = group.add("edittext", undefined, "", { multiline: true, scrollable: true });
        edit.preferredSize = [self.contentWidth(), self.promptHeight()];
        var actions = group.add("group{orientation:'row',alignChildren:['left','center'],spacing:0,margins:0}"),
            translate = actions.add("button");
        actions.preferredSize = actions.minimumSize = actions.maximumSize = [self.contentWidth(), self.presetButtonSize];
        translate.alignment = ["left", "center"];
        var context = schema.id == "negative_prompt" ? "negative" : "positive",
            forgePositive = context == "positive" && schema.backend == BACKEND_FORGE,
            loras = normalizeForgeLoraList(schema.forgeLoras),
            loraButton = null,
            presetStore = cfg.getPromptPresetStore(context);
        if (forgePositive) {
            loraButton = actions.add("button");
            loraButton.text = "+ " + localize(str.lora);
            loraButton.helpTip = localize(str.selectLora);
            loraButton.enabled = loras.length > 0;
            loraButton.onClick = function () {
                var selected = handlers && handlers.selectForgeLora ? handlers.selectForgeLora(loras) : "";
                if (!selected) return;
                var tag = "<lora:" + selected + ":1>", current = String(edit.text || "");
                edit.text = tag + (current ? " " : "") + current;
                updateControlState();
            };
        }
        var loraWidth = loraButton ? self.presetButtonSize * 2 : 0,
            translateWidth = self.contentWidth() - loraWidth;
        translate.preferredSize = translate.minimumSize = translate.maximumSize = [translateWidth, self.presetButtonSize];
        if (loraButton)
            loraButton.preferredSize = loraButton.minimumSize = loraButton.maximumSize = [loraWidth, self.presetButtonSize];
        title.text = self.label(schema);
        title.helpTip = self.help(schema);
        translate.text = localize(str.translate) + " → EN";
        translate.helpTip = localize(str.translatePromptHelp);
        edit.text = String(storedValue === undefined ? "" : storedValue);
        fillPresets();
        updateControlState();
        presetList.onChange = function () {
            var presetText = selectedPresetText();
            edit.text = presets.applyPrompt(context, edit.text, presetText);
            updateControlState();
        };
        refresh.onClick = function () {
            edit.text = "";
            updateControlState();
        };
        add.onClick = function () {
            var currentName = presetList.selection ? presetList.selection.text : localize(str.presetDefault),
                name = prompt(localize(str.presetNamePrompt), currentName + localize(str.presetCopy), localize(str.presetNew));
            name = name == null ? "" : String(name).replace(/^\s+|\s+$/g, "");
            if (!name) return;
            if (String(name).toLowerCase() == String(localize(str.presetDefault)).toLowerCase()) {
                alert(localize(str.errDefaultPreset));
                return;
            }
            if (presetStore.hasOwnProperty(name) && !confirm(localize(str.errPreset, name), false, localize(str.presetNew))) return;
            presetStore[name] = presets.promptText(context, edit.text);
            fillPresets(name);
            updateControlState();
        };
        save.onClick = function () {
            if (!presetList.selection || presetList.selection.index == 0) return;
            presetStore[presetList.selection.text] = presets.promptText(context, edit.text);
            updateControlState();
        };
        remove.onClick = function () {
            if (!presetList.selection || presetList.selection.index == 0) return;
            var index = presetList.selection.index,
                name = presetList.selection.text;
            if (!confirm(localize(str.presetDeleteConfirmA) + name + localize(str.presetDeleteConfirmB))) return;
            delete presetStore[name];
            fillPresets(null, Math.max(0, index - 1));
            updateControlState();
        };
        edit.onChanging = function () { updateControlState(); };
        translate.onClick = function () {
            if (!edit.text.length) return;
            try {
                var translated = self.runWithPaletteProgress(str.progressTranslate, function (progress) {
                    return api.translate(String(edit.text).replace(/\r?\n/g, " "), progress);
                });
                if (translated && String(translated).length) {
                    edit.text = translated;
                    updateControlState();
                } else {
                    self.showErrorMessage(localize(str.errTranslate));
                }
            } catch (e) {
                self.showErrorMessage((e && e.message ? e.message : localize(str.errTranslate)));
            }
        };
        return { getValue: function () { return edit.text; }, control: edit, container: group };
        function selectedPresetText() {
            return presetList.selection && presetList.selection.index > 0
                ? String(presetStore[presetList.selection.text] || "")
                : "";
        }
        function updateControlState() {
            var current = presets.promptText(context, edit.text),
                stored = selectedPresetText(),
                changed = current != stored,
                customPreset = !!(presetList.selection && presetList.selection.index > 0);
            translate.enabled = edit.text.length > 0;
            remove.enabled = customPreset;
            save.enabled = customPreset && changed;
            refresh.enabled = changed;
            add.enabled = current.length > 0;
            if (loraButton) loraButton.enabled = loras.length > 0;
        }
        function fillPresets(selectName, selectIndex) {
            presetList.removeAll();
            presetList.add("item", localize(str.presetDefault));
            var names = [], key, i, selected = 0;
            for (key in presetStore) if (presetStore.hasOwnProperty(key)) names.push(key);
            names.sort(function (a, b) {
                a = String(a).toLowerCase(); b = String(b).toLowerCase();
                return a == b ? 0 : (a > b ? 1 : -1);
            });
            for (i = 0; i < names.length; i++) {
                presetList.add("item", names[i]);
                if (names[i] == selectName) selected = i + 1;
            }
            if (selectName == null && selectIndex != null) selected = Math.min(Math.max(0, selectIndex), presetList.items.length - 1);
            presetList.selection = selected;
        }
    }
    function addImageReferenceControls(parent, schema, profile) {
        var bindings = schema && schema.bindings ? (schema.bindings.reference_images || []) : [];
        if (!bindings.length) return;
        if (!profile.referenceFiles) profile.referenceFiles = {};
        cfg.cleanReferenceHistory();
        for (var i = 0; i < bindings.length; i++) addReferenceControl(parent, bindings[i], i, bindings.length, profile);
    }
    function addHistoryFileDropdown(parent, options) {
        var group = self.addColumn(parent, 0);
        group.preferredSize.width = group.minimumSize.width = group.maximumSize.width = self.contentWidth();
        if (options.title) {
            var title = group.add("statictext");
            title.text = options.title;
        }
        var dropdown = group.add("dropdownlist{preferredSize:[" + self.contentWidth() + ",-1]}");
        dropdown.minimumSize.width = dropdown.maximumSize.width = self.contentWidth();
        dropdown.helpTip = options.helpTip || "";
        function currentPath() {
            var path = options.getValue() || "";
            if (path && !(new File(path)).exists) {
                options.setValue("");
                path = "";
            }
            return path;
        }
        function rebuild(selectedPath) {
            dropdown.removeAll();
            var noneItem = dropdown.add("item", localize(str.noneReference));
            noneItem.filePath = "";
            var history = cfg.cleanReferenceHistory().slice(0),
                selectedIndex = 0;
            for (var i = 0; i < history.length; i++) {
                var item = dropdown.add("item", shortenReferencePath(history[i]));
                item.filePath = history[i];
                if (selectedPath && String(history[i]).toUpperCase() == String(selectedPath).toUpperCase())
                    selectedIndex = i + 1;
            }
            var browseItem = dropdown.add("item", localize(str.browse));
            browseItem.browse = true;
            dropdown.selection = Math.min(selectedIndex, dropdown.items.length - 1);
        }
        rebuild(currentPath());
        dropdown.onChange = function () {
            if (!this.selection) return;
            if (this.selection.browse) {
                var file = (new File(" ")).openDlg(localize(str.selectReferenceImage), "*.jpg,*.jpeg,*.png,*.webp");
                if (!file) {
                    rebuild(currentPath());
                    return;
                }
                options.setValue(file.fsName);
                cfg.rememberReference(file.fsName);
                rebuild(file.fsName);
                return;
            }
            options.setValue(this.selection.filePath || "");
        };
        return group;
    }
    function addReferenceControl(parent, binding, index, total, profile) {
        return addHistoryFileDropdown(parent, {
            title: total > 1
                ? localize(str.imageReference) + " " + (index + 1) + " — " + (binding.label || binding.id)
                : localize(str.imageReference),
            helpTip: binding.label || binding.id,
            getValue: function () { return profile.referenceFiles[binding.id] || ""; },
            setValue: function (value) { profile.referenceFiles[binding.id] = value || ""; }
        });
    }
    function addForgeImageStitchControls(parent, schema, profile, controls, onVisibilityChanged) {
        var capabilities = schema && schema.capabilities ? schema.capabilities : {};
        if (!capabilities.image_stitch) return;
        if (!profile.imageStitchInputs || !(profile.imageStitchInputs instanceof Array)) profile.imageStitchInputs = ["", "", ""];
        while (profile.imageStitchInputs.length < 3) profile.imageStitchInputs.push("");
        cfg.cleanReferenceHistory();
        var enabled = profile.values.hasOwnProperty("image_stitch") ? !!profile.values.image_stitch : !!schema.image_stitch_default,
            checkbox = parent.add("checkbox");
        checkbox.text = localize(str.imageStitchInputs);
        checkbox.value = enabled;
        controls.image_stitch = { getValue: function () { return checkbox.value; }, control: checkbox };
        if (enabled) for (var index = 0; index < 3; index++) addForgeImageInput(parent, profile, index);
        checkbox.onClick = function () {
            profile.values.image_stitch = this.value;
            if (onVisibilityChanged) onVisibilityChanged(this.value);
        };
    }
    function addForgeImageInput(parent, profile, index) {
        return addHistoryFileDropdown(parent, {
            helpTip: localize(str.imageStitchInput) + " " + (index + 1),
            getValue: function () { return profile.imageStitchInputs[index] || ""; },
            setValue: function (value) { profile.imageStitchInputs[index] = value || ""; }
        });
    }
    function shortenReferencePath(path) {
        var separator = path.indexOf("\\") >= 0 ? "\\" : "/",
            parts = String(path).split(separator);
        if (parts.length <= 2) return path;
        var result = [parts[0]], current = parts[0].length, tail = parts[parts.length - 1];
        for (var i = 1; i < parts.length - 1; i++) {
            if (current + parts[i].length + tail.length < 36) { result.push(parts[i]); current += parts[i].length; }
            else { result.push("..."); break; }
        }
        result.push(tail);
        return result.join(separator);
    }
    function addResizeControl(parent, bounds, profile, schema) {
        if (profile.autoResize === undefined) profile.autoResize = cfg.autoResize;
        if (profile.manualScale === undefined) profile.manualScale = 1;
        if (profile.resize === undefined) profile.resize = 1;
        if (!profile.resizePreset) profile.resizePreset = presets.normalizeResizeName(profile.resizePreset, cfg.resizePresets);
        var group = parent.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:0}");
        group.preferredSize.width = group.minimumSize.width = group.maximumSize.width = self.contentWidth();
        var titleRow = group.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}");
        titleRow.preferredSize.width = titleRow.minimumSize.width = titleRow.maximumSize.width = self.contentWidth();
        var checkbox = titleRow.add("checkbox"),
            resizeTitleWidth = self.contentWidth() - self.autoResizeCheckboxWidth - self.sliderValueWidth-10,
            title = titleRow.add("statictext"),
            valueText = titleRow.add("statictext{justify:'right'}");
        checkbox.preferredSize.width = checkbox.minimumSize.width = checkbox.maximumSize.width = self.autoResizeCheckboxWidth;
        title.preferredSize.width = title.minimumSize.width = title.maximumSize.width = resizeTitleWidth;
        valueText.preferredSize.width = valueText.minimumSize.width = valueText.maximumSize.width = self.sliderValueWidth;
        var slider = group.add("slider{minvalue:1,maxvalue:400}");
        slider.preferredSize.width = slider.minimumSize.width = slider.maximumSize.width = self.contentWidth();
        var presetGroup = group.add("group{orientation:'column',alignChildren:['fill','center'],spacing:0,margins:[0,5,0,0]}");
        presetGroup.preferredSize.width = presetGroup.minimumSize.width = presetGroup.maximumSize.width = self.contentWidth();
        var presetDropdown = presetGroup.add("dropdownlist{preferredSize:[" + self.contentWidth() + ",-1]}");
        presetDropdown.minimumSize.width = presetDropdown.maximumSize.width = self.contentWidth();
        checkbox.value = profile.autoResize;
        checkbox.helpTip = str.autoResize;
        presetDropdown.helpTip = str.resizePreset;
        title.helpTip = schema.has_size_binding ? str.sizeWorkflowBinding : str.sizeFromInput;
        fillPresetList();
        setSliderValue();
        slider.onChange = function () {
            var sliderValue = Math.floor(this.value);
            profile.resize = (sliderValue >= 97 && sliderValue <= 103) ? 1 : Math.max(0.01, sliderValue / 100);
            if (!checkbox.value) profile.manualScale = profile.resize;
            valueText.text = profile.resize.toFixed(2);
            title.text = setTitle();
            isDirty = true;
        };
        slider.onChanging = function () { this.onChange(); };
        checkbox.onClick = function () {
            profile.autoResize = this.value;
            presetGroup.enabled = this.value;
            setSliderValue();
        };
        presetDropdown.onChange = function () {
            if (!this.selection) return;
            profile.resizePreset = cfg.resizePresets[this.selection.index].name;
            setSliderValue();
            isDirty = true;
        };
        presetGroup.enabled = checkbox.value;
        function fillPresetList() {
            presetDropdown.removeAll();
            for (var i = 0; i < cfg.resizePresets.length; i++) presetDropdown.add("item", presets.formatResize(cfg.resizePresets[i]));
            var preset = presets.findResize(profile.resizePreset, cfg.resizePresets),
                selected = presets.findResizeIndex(preset.name, cfg.resizePresets);
            presetDropdown.selection = selected < 0 ? 0 : selected;
            profile.resizePreset = preset.name;
        }
        function setTitle() {
            var scale = profile.autoResize ? profile.resize : profile.manualScale,
                size = calculateSizeFromScale(bounds.width, bounds.height, scale, profile.sizeMultiple || cfg.sizeMultiple);
            var text = profile.autoResize ? str.autoResize : str.resize,
                mp = Math.floor(size.width * size.height / 10000) / 100;
            return scale != 1
                ? text + ": " + size.width + "x" + size.height + " (" + mp + " MP)"
                : text;
        }
        function setSliderValue() {
            if (profile.autoResize) {
                var scale = autoScale(bounds, presets.findResize(profile.resizePreset, cfg.resizePresets));
                profile.resize = scale;
                slider.value = scale * 100;
                valueText.text = scale.toFixed(2);
                title.text = setTitle();
            } else {
                slider.value = profile.manualScale * 100;
                profile.manualScale = Math.floor(slider.value) / 100;
                valueText.text = profile.manualScale.toFixed(2);
                title.text = setTitle();
            }
        }
    }
    function runWithPaletteProgress(title, fn) {
        var progress = new StartupProgress(title || str.progressInitializing, ANALYZE_TIMEOUT);
        try {
            progress.show();
            progress.setStage(title || str.progressInitializing, 10);
            var result = fn(progress);
            progress.complete();
            return result;
        } finally {
            progress.close();
        }
    }
    function showErrorMessage(value, title) {
        var text = errorMessageText(value), dialogTitle = title || APP.name;
        if (text.length <= 300) {
            alert(text, dialogTitle, true);
            return;
        }
        try { app.beep(); } catch (_) { }
        var w = new Window("dialog{orientation:'column',alignChildren:['fill','top'],spacing:10,margins:15}"),
            heading = w.add("statictext", undefined, localize(str.errorOccurred)),
            explanation = w.add("statictext", undefined, localize(str.errorDialogIntro), { multiline: true }),
            details = w.add("panel", undefined, localize(str.errorDetails)),
            message = details.add("edittext", undefined, text, { multiline: true, scrollable: true, readonly: true }),
            buttons = w.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,5,0,0]}"),
            ok = buttons.add("button", undefined, "OK", { name: "ok" });
        w.text = dialogTitle + " — " + localize(str.errorDialogTitle);
        try { heading.graphics.font = ScriptUI.newFont(heading.graphics.font.name, "BOLD", 15); } catch (_) { }
        explanation.preferredSize.width = 700;
        details.orientation = "column";
        details.alignChildren = ["fill", "fill"];
        details.margins = 12;
        message.preferredSize = [700, 360];
        message.minimumSize = [540, 260];
        message.readonly = true;
        self.enableHoverFocus(w);
        w.center();
        w.show();
    }
    function StartupProgress(message, timeout) {
        var w = new Window("palette", APP.name);
        w.orientation = "column";
        w.alignChildren = ["fill", "top"];
        w.spacing = 5;
        w.margins = 15;
        var text = w.add("statictext");
        text.preferredSize = [420, -1];
        var bar = w.add("progressbar", undefined, 0, 100);
        bar.preferredSize = [420, 15];
        var currentMessage = message,
            baseValue = 2;
        var stageStarted = (new Date()).getTime(),
            totalTimeout = Math.max(1000, timeout || START_TIMEOUT);
        text.text = localize(currentMessage);
        bar.value = baseValue;
        this.show = function () {
            w.center();
            w.show();
            w.update();
        };
        this.setStage = function (newMessage, value) {
            currentMessage = newMessage || currentMessage;
            baseValue = Math.max(bar.value, Math.min(96, value === undefined ? bar.value : value));
            stageStarted = (new Date()).getTime();
            bar.value = baseValue;
            text.text = localize(currentMessage);
            w.update();
        };
        this.pulse = function () {
            var elapsed = (new Date()).getTime() - stageStarted,
                addition = Math.min(12, elapsed / totalTimeout * 70);
            bar.value = Math.min(97, baseValue + addition);
            text.text = localize(currentMessage) + "  " + roundTo(elapsed / 1000, 1) + " " + localize(str.secondsShort);
            w.update();
        };
        this.complete = function () {
            bar.value = 100;
            text.text = localize(str.progressReady);
            w.update();
        };
        this.close = function () { try { w.close(); } catch (_) { } };
    }
    function DelayedStartupProgress(message, timeout, delay) {
        var inner = new StartupProgress(message, timeout),
            shown = false,
            started = (new Date()).getTime(),
            currentMessage = message,
            currentValue = 2;
        delay = Math.max(0, Number(delay) || 0);
        function ensureShown(force) {
            if (shown) return true;
            if (!force && (new Date()).getTime() - started < delay) return false;
            inner.show();
            inner.setStage(currentMessage, currentValue);
            shown = true;
            return true;
        }
        this.show = function () { ensureShown(true); };
        this.setStage = function (nextMessage, value) {
            currentMessage = nextMessage || currentMessage;
            if (value !== undefined) currentValue = value;
            if (shown) inner.setStage(currentMessage, currentValue);
        };
        this.pulse = function () {
            if (ensureShown(false)) inner.pulse();
        };
        this.complete = function () {
            if (shown) inner.complete();
        };
        this.close = function () {
            if (shown) inner.close();
        };
    }
    this.addImageReferenceControls = addImageReferenceControls;
    this.addForgeImageStitchControls = addForgeImageStitchControls;
    this.addResizeControl = addResizeControl;
    this.runWithPaletteProgress = runWithPaletteProgress;
    this.showErrorMessage = showErrorMessage;
    this.createStartupProgress = function (message, timeout) { return new StartupProgress(message, timeout); };
    this.createDelayedStartupProgress = function (message, timeout, delay) { return new DelayedStartupProgress(message, timeout, delay); };
}
function GenerationProgress() {
    var payload = null,
        result = null,
        firstAnswer = null,
        prepareTitle = "",
        generateTitle = "",
        delayKey = "",
        delayMax = 7500,
        requestId = null;
    this.begin = function (options) {
        options = options || {};
        payload = options.command || null;
        result = null;
        firstAnswer = null;
        prepareTitle = options.titles && options.titles.prepare ? options.titles.prepare : "";
        generateTitle = options.titles && options.titles.generate ? options.titles.generate : "";
        delayKey = options.timingKey || "";
        delayMax = options.timingMax || 7500;
        requestId = options.requestId || (payload ? payload.request_id : null);
    };
    this.run = function () {
        if (!app.doProgressSegmentTask(
            GENERATION_PREPARE_SEGMENT, 0, 100, "generationStageOne()"
        )) {
            $.setenv(APP.dialogEnvKey, "true");
            api.interrupt(requestId);
            throw new Error(APP.cancelToken);
        }
        if (!firstAnswer || firstAnswer.type == "error" || firstAnswer.message != "init") {
            result = firstAnswer;
            return true;
        }
        if (!app.doProgressSegmentTask(
            GENERATION_RUN_SEGMENT, GENERATION_PREPARE_SEGMENT, 100,
            "generationStageTwo()"
        )) {
            $.setenv(APP.dialogEnvKey, "true");
            api.interrupt(requestId);
            throw new Error(APP.cancelToken);
        }
        return true;
    };
    this.stageOne = function () {
        var prepareTimeout = payload && payload.type == "forge_generate" ? 5 * 60 * 1000 : 120000,
            answer = api.startGeneration({
                command: payload,
                timeout: prepareTimeout,
                title: prepareTitle || localize(str.progressPrepare)
            });
        if (answer === false) return false;
        firstAnswer = answer;
        return true;
    };
    this.stageTwo = function () {
        var answer = api.finishGeneration({
            timeout: cfg.generationTimeout * 1000,
            title: generateTitle || localize(str.progressGenerate),
            max: delayMax,
            delayKey: delayKey,
            requestId: requestId
        });
        result = answer === false ? false : answer;
        return answer !== false;
    };
    this.getResult = function () { return result; };
    this.getRequestId = function () { return requestId; };
    this.clear = function () {
        payload = null;
        result = null;
        firstAnswer = null;
        prepareTitle = "";
        generateTitle = "";
        delayKey = "";
        delayMax = 7500;
        requestId = null;
    };
}
function prepareSelectionLayer(selection) { return generation.prepareSelectionLayer(selection); }
function checkSelection(result) { return generation.checkSelection(result); }
function runGenerationProgress() { return generationProgress.run(); }
function generationStageOne() { return generationProgress.stageOne(); }
function generationStageTwo() { return generationProgress.stageTwo(); }
function fitSelectionBounds(result, multiple) {
    multiple = clamp(parseInt(multiple, 10) || 1, 1, 256);
    if (!result.sourceBounds) result.sourceBounds = cloneObject(result.bounds);
    var source = result.sourceBounds,
        b = result.bounds,
        resolution = doc.getProperty("resolution");
    b.top = source.top;
    b.left = source.left;
    b.right = source.right;
    b.bottom = source.bottom;
    var canvas = {
        top: 0,
        left: 0,
        right: Math.round(doc.getProperty("width") * resolution / 72),
        bottom: Math.round(doc.getProperty("height") * resolution / 72)
    };
    var clipped = b.top < canvas.top || b.left < canvas.left || b.right > canvas.right || b.bottom > canvas.bottom;
    b.top = Math.max(canvas.top, Math.round(b.top));
    b.left = Math.max(canvas.left, Math.round(b.left));
    b.right = Math.min(canvas.right, Math.round(b.right));
    b.bottom = Math.min(canvas.bottom, Math.round(b.bottom));
    if (b.right <= b.left || b.bottom <= b.top) {
        b.top = canvas.top;
        b.left = canvas.left;
        b.right = canvas.right;
        b.bottom = canvas.bottom;
        clipped = true;
    }
    fitAxis("left", "right", canvas.right);
    fitAxis("top", "bottom", canvas.bottom);
    b.width = b.right - b.left;
    b.height = b.bottom - b.top;
    if (clipped && doc.hasProperty("selection")) doc.makeSelection(b);
    function fitAxis(startKey, endKey, limit) {
        var start = b[startKey],
            end = b[endKey];
        var size = end - start,
            target = Math.floor(size / multiple) * multiple;
        if (!target) target = size;
        if (target >= size) {
            start = Math.round((start + end - target) / 2);
            start = Math.max(0, Math.min(start, limit - target));
        } else {
            start += Math.floor((size - target) / 2);
        }
        b[startKey] = start;
        b[endKey] = start + target;
    }
}
function LayerMetadata() {
    var current = null;
    function ensureLibrary() {
        try {
            if (ExternalObject.AdobeXMPScript == undefined)
                ExternalObject.AdobeXMPScript = new ExternalObject("lib:AdobeXMPScript");
            XMPMeta.registerNamespace(APP.xmp.namespace, APP.xmp.prefix);
            return true;
        } catch (_) { return false; }
    }
    this.set = function (value) { current = cloneObject(value); };
    this.write = function () {
        if (!ensureLibrary() || !current) return false;
        try {
            var xmp;
            try { xmp = new XMPMeta(app.activeDocument.activeLayer.xmpMetadata.rawData); }
            catch (_) { xmp = new XMPMeta(); }
            xmp.setProperty(APP.xmp.namespace, APP.xmp.property, jsonStringify(current));
            app.activeDocument.activeLayer.xmpMetadata.rawData = xmp.serialize();
            return true;
        } catch (_) { return false; }
    };
    this.read = function () {
        if (!ensureLibrary() || !app.documents.length) return null;
        try {
            var xmp = new XMPMeta(app.activeDocument.activeLayer.xmpMetadata.rawData);
            if (!xmp.doesPropertyExist(APP.xmp.namespace, APP.xmp.property)) return null;
            var source = xmp.getProperty(APP.xmp.namespace, APP.xmp.property).value.toString(),
                value = null;
            try { value = jsonParse(source); }
            catch (_) { try { value = eval("(" + source + ")"); } catch (_) { value = null; } }
            if (!value || typeof value != "object") return null;
            if (value.backend == BACKEND_COMFY && !value.workflow_id && !value.relative_path) return null;
            if (value.backend == BACKEND_FORGE && !value.workspace_id && !value.schema_id) return null;
            return value;
        } catch (_) { }
        return null;
    };
}
function BridgeApi() {
    var self = this;
    this.isRunning = function () { return checkConnection(API_HOST, API_PORT_SEND); };
    this.initialize = function (progress) {
        var pythonFile = findPythonModule();
        if (!pythonFile) throw new Error(str.errPythonMissingA + API_FILE + str.errPythonMissingB);
        if (self.isRunning()) {
            var running = self.ping(progress);
            if (String(running.protocol) != String(API_PROTOCOL)) {
                throw new Error(localize(str.errApiProtocolA) + running.protocol + localize(str.errApiProtocolB) + API_PROTOCOL + ".");
            }
            return true;
        }
        if (progress) progress.setStage(str.progressStartPython, 3);
        pythonFile.execute();
        if (!waitForConnection(START_TIMEOUT, progress)) {
            throw new Error(str.errPythonStartA + API_HOST + ":" + API_PORT_SEND + str.errPythonStartB);
        }
        var started = self.ping(progress);
        if (String(started.protocol) != String(API_PROTOCOL)) {
            throw new Error(localize(str.errApiProtocolA) + started.protocol + localize(str.errApiProtocolB) + API_PROTOCOL + ".");
        }
        return true;
    };
    this.ping = function (progress, timeout) {
        return unwrapAnswer(request(makeCommand("ping", {}), timeout || SHORT_TIMEOUT, progress));
    };
    this.translate = function (text, progress) {
        return unwrapAnswer(request(makeCommand("translate", { text: text || "" }), TRANSLATE_TIMEOUT, progress));
    };
    this.handshake = function (progress, settings, refreshBackends) {
        var source = settings || cfg;
        return unwrapAnswer(request({
            protocol: API_PROTOCOL,
            request_id: createRequestId(),
            type: "handshake",
            message: {
                host: source.backendHost,
                comfyPort: source.comfyPort,
                forgePort: source.forgePort,
                workflowsFolder: source.workflowsFolder,
                generationTimeout: source.generationTimeout,
                refreshBackends: !!refreshBackends
            }
        }, SHORT_TIMEOUT, progress));
    };
    this.probeBackends = function (settings, progress) {
        var source = settings || cfg;
        return unwrapAnswer(request(makeCommand("probe_backends", {
            host: source.backendHost,
            comfyPort: source.comfyPort,
            forgePort: source.forgePort
        }), SHORT_TIMEOUT, progress));
    };
    this.workflowList = function (progress) {
        return unwrapAnswer(request(makeCommand("workflow_list", {}), ANALYZE_TIMEOUT, progress));
    };
    this.forgeSchemaList = function (progress) {
        return unwrapAnswer(request(makeCommand("forge_schema_list", {
            schema_folder: cfg.forgeSchemasFolder || ""
        }), ANALYZE_TIMEOUT, progress));
    };
    this.forgeSchemaGet = function (schemaId, progress) {
        return unwrapAnswer(request(makeCommand("forge_schema_get", {
            schema_id: schemaId,
            schema_folder: cfg.forgeSchemasFolder || ""
        }), ANALYZE_TIMEOUT, progress));
    };
    this.forgeCatalog = function (sources, force, progress) {
        return unwrapAnswer(request(makeCommand("forge_catalog", {
            sources: sources instanceof Array ? sources : [],
            force: !!force
        }), 5 * 60 * 1000, progress));
    };
    this.workflowGet = function (workflowId, overrides, relativePath, progress) {
        return unwrapAnswer(request(makeCommand("workflow_get", {
            workflow_id: workflowId,
            relative_path: relativePath || "",
            binding_overrides: cleanBindingOverrides(overrides)
        }), ANALYZE_TIMEOUT, progress));
    };
    this.workflowReinitialize = function (workflowId, overrides, relativePath, progress) {
        return unwrapAnswer(request(makeCommand("workflow_reinitialize", {
            workflow_id: workflowId,
            relative_path: relativePath || "",
            binding_overrides: cleanBindingOverrides(overrides),
            force: true
        }), ANALYZE_TIMEOUT, progress));
    };
    this.interrupt = function (requestId) {
        try { fire(makeCommand("interrupt", { request_id: requestId || "" }, requestId)); } catch (_) { }
    };
    this.startGeneration = function (options) {
        options = options || {};
        return requestWithOptions(options.command, {
            timeout: options.timeout,
            title: options.title,
            max: options.timeout
        });
    };
    this.finishGeneration = function (options) {
        options = options || {};
        return waitForAnswerAfterAck({
            timeout: options.timeout,
            title: options.title,
            max: options.max,
            trackDelay: true,
            delayKey: options.delayKey,
            requestId: options.requestId
        });
    };
    function request(command, timeout, progress) {
        return requestWithOptions(command, {
            timeout: timeout,
            progress: progress
        });
    }
    function requestWithOptions(command, options) {
        options = options || {};
        var listener = new Socket();
        if (!listener.listen(API_PORT_LISTEN, "UTF-8")) throw new Error(str.errListenerPort + API_PORT_LISTEN + ".");
        try {
            sendCommand(command);
            options.expectedRequestId = command.request_id;
            return pollListener(listener, options);
        } finally {
            try { listener.close(); } catch (_) { }
        }
    }
    function waitForAnswerAfterAck(options) {
        options = options || {};
        var listener = new Socket();
        if (!listener.listen(API_PORT_LISTEN, "UTF-8")) throw new Error(str.errListenerPort + API_PORT_LISTEN + ".");
        try {
            fire(makeCommand("ack", {}, options.requestId));
            options.expectedRequestId = options.requestId;
            return pollListener(listener, options);
        } finally { try { listener.close(); } catch (_) { } }
    }
    function fire(command) { sendCommand(command); }
    function sendCommand(command) {
        var sender = new Socket();
        if (!sender.open(API_HOST + ":" + API_PORT_SEND, "UTF-8")) throw new Error(str.errApiConnection);
        try { sender.writeln(jsonStringify(command)); }
        finally { sender.close(); }
    }
    function pollListener(listener, options) {
        options = options || {};
        var timeout = options.timeout || SHORT_TIMEOUT,
            title = options.title,
            progress = options.progress,
            max = options.max,
            trackDelay = !!options.trackDelay,
            delayKey = options.delayKey,
            expectedRequestId = options.expectedRequestId,
            t1 = (new Date()).getTime(),
            t2 = t1,
            t3 = t1,
            slice = 0;
        if (title) {
            max = Number(max) || timeout || 7500;
            if (max < 1) max = 1;
            slice = 1 / max * PROGRESS_TASK_RANGE;
        }
        for (; ;) {
            t2 = (new Date()).getTime();
            if (t2 - t1 > timeout) {
                listener.close();
                throw new Error(str.errApiTimeout);
            }
            if (progress) progress.pulse();
            if (title && t2 - t3 >= 1) {
                t3 = t2;
                var text = trackDelay
                    ? title + "\t " + Math.floor((t2 - t1) / 100) / 10 + " s. "
                    : title;
                if (!app.doProgressTask(slice, "workChunk('" + escapeProgressText(text) + "');")) {
                    $.setenv(APP.dialogEnvKey, "true");
                    try { self.interrupt(expectedRequestId); } catch (_) { }
                    listener.close();
                    return false;
                }
            }
            var connection = listener.poll();
            if (connection != null) {
                var answer = null,
                    rawAnswer = "";
                try {
                    rawAnswer = connection.readln();
                    answer = jsonParse(rawAnswer);
                } catch (parseError) {
                    connection.close();
                    listener.close();
                    throw new Error(localize(str.errApiInvalidAnswer) + " " + parseError.message +
                        " (" + rawAnswer.length + " chars)");
                }
                connection.close();
                if (!answer) {
                    listener.close();
                    throw new Error(str.errEmptyApiAnswer);
                }
                if (expectedRequestId && answer.request_id && String(answer.request_id) != String(expectedRequestId)) continue;
                listener.close();
                if (trackDelay && delayKey) generationTimings.saveDelay(delayKey, t2 - t1);
                return answer;
            }
            $.sleep(1);
        }
    }
    function workChunk(text) {
        app.changeProgressText(text);
        $.sleep(0);
    }
    function escapeProgressText(text) {
        return String(text)
            .replace(/\\/g, "\\\\")
            .replace(/'/g, "\\'")
            .replace(/[\r\n]+/g, " ");
    }
    function findPythonModule() {
        var base = (new File($.fileName)).parent,
            candidates = [
                new File(base.fsName + "/" + API_FILE + ".pyw"),
                new File(base.fsName + "/" + API_FILE + ".py"),
                new File(base.fsName + "/lib/" + API_FILE + ".pyw"),
                new File(base.fsName + "/lib/" + API_FILE + ".py")
            ];
        for (var i = 0; i < candidates.length; i++) if (candidates[i].exists) return candidates[i];
        return null;
    }
    function waitForConnection(timeout, startup) {
        var started = (new Date()).getTime();
        while ((new Date()).getTime() - started < timeout) {
            if (checkConnection(API_HOST, API_PORT_SEND)) return true;
            if (startup) startup.pulse();
            $.sleep(25);
        }
        return false;
    }
    function checkConnection(host, port) {
        var socket = new Socket();
        try { return socket.open(host + ":" + port, "UTF-8"); }
        catch (_) { return false; }
        finally { try { socket.close(); } catch (_) { } }
    }
    function cleanBindingOverrides(value) {
        if (!value) return {};
        var result = {};
        if (value.input) result.input = value.input;
        if (value.mask) result.mask = value.mask;
        if (value.references instanceof Array && value.references.length) result.references = value.references.slice(0);
        if (value.output) result.output = value.output;
        if (value.size) result.size = value.size;
        return result;
    }
    function makeCommand(type, message, requestId) {
        return { protocol: API_PROTOCOL, request_id: requestId || createRequestId(), type: type, message: message || {} };
    }
    function unwrapAnswer(response) {
        if (!response) throw new Error(str.errEmptyApiAnswer);
        if (response.type == "error") throw new Error(response.message);
        return response.message;
    }
}
function DescriptorCodec() {
    function readDescriptor(target, descriptor) {
        for (var i = 0; i < descriptor.count; i++) {
            var key = descriptor.getKey(i),
                name = t2s(key),
                type = descriptor.getType(key);
            if (type == DescValueType.BOOLEANTYPE) target[name] = descriptor.getBoolean(key);
            else if (type == DescValueType.STRINGTYPE) target[name] = descriptor.getString(key);
            else if (type == DescValueType.INTEGERTYPE) target[name] = descriptor.getInteger(key);
            else if (type == DescValueType.LARGEINTEGERTYPE) target[name] = descriptor.getLargeInteger(key);
            else if (type == DescValueType.DOUBLETYPE) target[name] = descriptor.getDouble(key);
            else if (type == DescValueType.OBJECTTYPE) {
                target[name] = {};
                readDescriptor(target[name], descriptor.getObjectValue(key));
            } else if (type == DescValueType.LISTTYPE) target[name] = readList(descriptor.getList(key));
        }
        return target;
    }
    function readList(list) {
        var result = [];
        for (var i = 0; i < list.count; i++) {
            var type = list.getType(i);
            if (type == DescValueType.BOOLEANTYPE) result.push(list.getBoolean(i));
            else if (type == DescValueType.STRINGTYPE) result.push(list.getString(i));
            else if (type == DescValueType.INTEGERTYPE) result.push(list.getInteger(i));
            else if (type == DescValueType.LARGEINTEGERTYPE) result.push(list.getLargeInteger(i));
            else if (type == DescValueType.DOUBLETYPE) result.push(list.getDouble(i));
            else if (type == DescValueType.OBJECTTYPE) result.push(readDescriptor({}, list.getObjectValue(i)));
            else if (type == DescValueType.LISTTYPE) result.push(readList(list.getList(i)));
        }
        return result;
    }
    function writeDescriptor(object, integerNumbers) {
        var descriptor = new ActionDescriptor();
        for (var name in object) if (object.hasOwnProperty(name)) {
            var value = object[name];
            if (value === null || value === undefined || typeof value == "function") continue;
            var key;
            try { key = s2t(String(name)); } catch (_) { continue; }
            if (typeof value == "boolean") descriptor.putBoolean(key, value);
            else if (typeof value == "string") descriptor.putString(key, value);
            else if (typeof value == "number") {
                if (integerNumbers && value == Math.round(value) && value >= -2147483648 && value <= 2147483647)
                    descriptor.putInteger(key, value);
                else descriptor.putDouble(key, value);
            } else if (value instanceof Array) descriptor.putList(key, writeList(value, integerNumbers));
            else if (typeof value == "object") descriptor.putObject(key, s2t("object"), writeDescriptor(value, integerNumbers));
        }
        return descriptor;
    }
    function writeList(array, integerNumbers) {
        var list = new ActionList();
        for (var i = 0; i < array.length; i++) {
            var value = array[i];
            if (value === null || value === undefined || typeof value == "function") continue;
            if (typeof value == "boolean") list.putBoolean(value);
            else if (typeof value == "string") list.putString(value);
            else if (typeof value == "number") {
                if (integerNumbers && value == Math.round(value) && value >= -2147483648 && value <= 2147483647)
                    list.putInteger(value);
                else list.putDouble(value);
            } else if (value instanceof Array) list.putList(writeList(value, integerNumbers));
            else if (typeof value == "object") list.putObject(s2t("object"), writeDescriptor(value, integerNumbers));
        }
        return list;
    }
    this.readInto = function (target, descriptor) { return readDescriptor(target || {}, descriptor); };
    this.toDescriptor = function (object, integerNumbers) { return writeDescriptor(object || {}, !!integerNumbers); };
}
function Config() {
    var self = this,
        keys = [
            "backendHost", "activeBackend", "comfyPort", "forgePort", "workflowsFolder", "forgeSchemasFolder", "selectedWorkflow", "selectedForgePreset",
            "autoResize", "sizeMultiple", "resizePresets",
            "flatten", "rasterizeImage", "keepAspectRatioDuringPlace", "recordSettingsToAction", "writeLayerMetadata",
            "selectBrush", "brushOpacity", "generationTimeout", "workflowProfiles", "forgeProfiles",
            "workflowCatalog", "forgeCatalog", "referenceHistory", "promptPresets"
        ];
    this.data = defaultData();
    this.bindProperties = function () {
        for (var i = 0; i < keys.length; i++) this[keys[i]] = this.data[keys[i]];
    };
    function syncData() {
        for (var i = 0; i < keys.length; i++) self.data[keys[i]] = self[keys[i]];
    }
    this.cleanReferenceHistory = function () {
        var source = self.referenceHistory instanceof Array ? self.referenceHistory : [], cleaned = [];
        for (var i = 0; i < source.length && cleaned.length < 10; i++) {
            var file = new File(source[i]);
            if (file.exists && !arrayContainsCaseInsensitive(cleaned, file.fsName)) cleaned.push(file.fsName);
        }
        self.referenceHistory = self.data.referenceHistory = cleaned;
        return cleaned;
    };
    this.rememberReference = function (path) {
        var file = new File(path || "");
        if (!file.exists) return;
        var current = self.cleanReferenceHistory(),
            result = [file.fsName];
        for (var i = 0; i < current.length && result.length < 10; i++)
            if (!arrayContainsCaseInsensitive(result, current[i])) result.push(current[i]);
        self.referenceHistory = self.data.referenceHistory = result;
    };
    this.load = function () {
        var file = new File(app.preferencesFolder + "/" + APP.settingsFile);
        if (!file.exists) { self.data = defaultData(); self.bindProperties(); self.cleanReferenceHistory(); return; }
        var fileOpened = false;
        try {
            file.open("r"); fileOpened = true; file.encoding = "BINARY";
            var stream = file.read(),
                descriptor = new ActionDescriptor(),
                loaded = {};
            file.close(); fileOpened = false;
            descriptor.fromStream(stream);
            descriptorCodec.readInto(loaded, descriptor);
            self.data = mergeDefaults(defaultData(), loaded);
        } catch (_) {
            self.data = defaultData();
            if (fileOpened) try { file.close(); } catch (_) { }
        }
        self.bindProperties();
        if (!self.resizePresets || !self.resizePresets.length) self.resizePresets = self.data.resizePresets = presets.defaultResize();
        self.cleanReferenceHistory();
    };
    this.loadFromAction = function () {
        var loaded = {};
        try { descriptorCodec.readInto(loaded, app.playbackParameters); }
        catch (_) { loaded = {}; }
        self.data = mergeDefaults(defaultData(), loaded);
        self.bindProperties();
        if (!self.resizePresets || !self.resizePresets.length) self.resizePresets = self.data.resizePresets = presets.defaultResize();
        self.cleanReferenceHistory();
    };
    this.saveToAction = function () {
        syncData();
        playbackParameters = descriptorCodec.toDescriptor(self.data);
    };
    this.save = function () {
        syncData();
        var descriptor = descriptorCodec.toDescriptor(self.data),
            file = new File(app.preferencesFolder + "/" + APP.settingsFile);
        var fileOpened = false;
        try {
            file.open("w"); fileOpened = true; file.encoding = "BINARY";
            file.write(descriptor.toStream());
            file.close(); fileOpened = false;
        } finally {
            if (fileOpened) try { file.close(); } catch (_) { }
        }
    };
    this.getProfile = function (workflowId) {
        if (!self.workflowProfiles) self.workflowProfiles = self.data.workflowProfiles = {};
        if (!self.workflowProfiles[workflowId]) {
            self.workflowProfiles[workflowId] = {
                relativePath: "",
                values: {},
                visibleControls: null,
                bindingOverrides: { input: "", mask: "", references: [], output: "", size: "" },
                referenceFiles: {},
                sizeMultiple: self.sizeMultiple,
                autoResize: self.autoResize,
                resizePreset: presets.normalizeResizeName("", self.resizePresets),
                resize: 1,
                manualScale: 1,
                schemaCache: null,
                schemaCacheStamp: null,
                schemaCacheVersion: 0
            };
        }
        var profile = self.workflowProfiles[workflowId];
        if (!profile.bindingOverrides) profile.bindingOverrides = { input: "", mask: "", references: [], output: "", size: "" };
        if (profile.bindingOverrides.mask === undefined) profile.bindingOverrides.mask = "";
        if (!(profile.bindingOverrides.references instanceof Array)) profile.bindingOverrides.references = [];
        if (!profile.referenceFiles) profile.referenceFiles = {};
        if (profile.sizeMultiple === undefined) profile.sizeMultiple = self.sizeMultiple;
        profile.sizeMultiple = clamp(parseInt(profile.sizeMultiple, 10) || self.sizeMultiple, 1, 256);
        if (!profile.resizePreset) profile.resizePreset = presets.normalizeResizeName("", self.resizePresets);
        if (profile.resize === undefined) profile.resize = 1;
        if (profile.manualScale === undefined) profile.manualScale = 1;
        return profile;
    };
    this.getForgeProfile = function (presetId) {
        if (!self.forgeProfiles) self.forgeProfiles = self.data.forgeProfiles = {};
        if (!self.forgeProfiles[presetId]) self.forgeProfiles[presetId] = {
            values: {}, visibleControls: null,
            imageStitchInputs: ["", "", ""],
            sizeMultiple: self.sizeMultiple, autoResize: self.autoResize,
            resizePreset: presets.normalizeResizeName("", self.resizePresets), resize: 1, manualScale: 1
        };
        var profile = self.forgeProfiles[presetId];
        if (!profile.values) profile.values = {};
        if (!(profile.imageStitchInputs instanceof Array)) profile.imageStitchInputs = ["", "", ""];
        while (profile.imageStitchInputs.length < 3) profile.imageStitchInputs.push("");
        if (profile.sizeMultiple === undefined) profile.sizeMultiple = self.sizeMultiple;
        profile.sizeMultiple = clamp(parseInt(profile.sizeMultiple, 10) || self.sizeMultiple, 1, 256);
        if (!profile.resizePreset) profile.resizePreset = presets.normalizeResizeName("", self.resizePresets);
        if (profile.resize === undefined) profile.resize = 1; if (profile.manualScale === undefined) profile.manualScale = 1;
        return profile;
    };
    this.getPromptPresetStore = function (context) {
        return presets.promptStore(self, context);
    };
    this.copySharedLibrariesFrom = function (sourceConfig) {
        self.referenceHistory = self.data.referenceHistory = cloneObject(sourceConfig && sourceConfig.referenceHistory instanceof Array ? sourceConfig.referenceHistory : []);
        self.cleanReferenceHistory();
        self.promptPresets = self.data.promptPresets = cloneObject(
            sourceConfig && sourceConfig.promptPresets ? sourceConfig.promptPresets : presets.defaultPrompt()
        );
        self.getPromptPresetStore("positive");
        self.getPromptPresetStore("negative");
    };
    this.copySharedLibrariesTo = function (targetConfig) {
        if (!targetConfig) return;
        targetConfig.referenceHistory = targetConfig.data.referenceHistory = cloneObject(self.referenceHistory instanceof Array ? self.referenceHistory : []);
        targetConfig.cleanReferenceHistory();
        targetConfig.promptPresets = targetConfig.data.promptPresets = cloneObject(self.promptPresets || presets.defaultPrompt());
        targetConfig.getPromptPresetStore("positive");
        targetConfig.getPromptPresetStore("negative");
    };
    this.setWorkflowCatalog = function (items) {
        self.workflowCatalog = self.data.workflowCatalog = items || [];
        for (var i = 0; i < self.workflowCatalog.length; i++) {
            var item = self.workflowCatalog[i];
            self.getProfile(item.id).relativePath = item.relative_path || "";
        }
    };
    this.getCachedSchema = function (workflowId, workflow) {
        var profile = self.getProfile(workflowId);
        if (!profile.schemaCache || !profile.schemaCacheStamp) return null;
        if (profile.schemaCacheVersion != APP.cache.schemaVersion) return null;
        if (profile.schemaCache.analysis_uuid != APP.cache.comfyAnalysisUuid) return null;
        var stamp = workflowStamp(workflow.relative_path || profile.relativePath);
        if (!stamp) return null;
        if (stamp.size != profile.schemaCacheStamp.size || stamp.modified != profile.schemaCacheStamp.modified) return null;
        return profile.schemaCache;
    };
    this.cacheSchema = function (schema, workflow) {
        if (!schema || !schema.workflow_id) return;
        for (var workflowId in self.workflowProfiles) if (self.workflowProfiles.hasOwnProperty(workflowId) && workflowId != schema.workflow_id) {
            self.workflowProfiles[workflowId].schemaCache = null;
            self.workflowProfiles[workflowId].schemaCacheStamp = null;
            self.workflowProfiles[workflowId].schemaCacheVersion = 0;
        }
        var profile = self.getProfile(schema.workflow_id);
        profile.relativePath = schema.relative_path || (workflow ? workflow.relative_path : profile.relativePath) || "";
        profile.schemaCache = schema;
        profile.schemaCacheStamp = workflowStamp(profile.relativePath);
        profile.schemaCacheVersion = APP.cache.schemaVersion;
    };
    this.bindProperties();
    if (!this.resizePresets || !this.resizePresets.length) this.resizePresets = this.data.resizePresets = presets.defaultResize();
    function workflowStamp(relativePath) {
        if (!relativePath) return null;
        var file = new File(self.workflowsFolder + "/" + relativePath);
        if (!file.exists) return null;
        return {
            size: file.length,
            modified: file.modified ? file.modified.getTime() : 0
        };
    }
    function defaultData() {
        return {
            backendHost: "127.0.0.1",
            activeBackend: BACKEND_COMFY,
            comfyPort: 8188,
            forgePort: 7860,
            workflowsFolder: "",
            forgeSchemasFolder: "",
            selectedWorkflow: "",
            selectedForgePreset: "sd",
            autoResize: true,
            sizeMultiple: 16,
            resizePresets: presets.defaultResize(),
            flatten: false,
            rasterizeImage: false,
            keepAspectRatioDuringPlace: false,
            recordSettingsToAction: true,
            writeLayerMetadata: false,
            selectBrush: true,
            brushOpacity: 50,
            generationTimeout: 1200,
            workflowProfiles: {},
            forgeProfiles: {},
            workflowCatalog: [],
            forgeCatalog: [],
            referenceHistory: [],
            promptPresets: presets.defaultPrompt()
        };
    }
    function mergeDefaults(defaults, loaded) {
        if (!loaded || typeof loaded != "object") return defaults;
        for (var key in loaded) if (loaded.hasOwnProperty(key)) {
            if (loaded[key] && typeof loaded[key] == "object" && !(loaded[key] instanceof Array) && defaults[key] && typeof defaults[key] == "object")
                defaults[key] = mergeDefaults(defaults[key], loaded[key]);
            else defaults[key] = loaded[key];
        }
        return defaults;
    }
}
function AM(target, order) {
    var AR = ActionReference, AD = ActionDescriptor;
    target = target ? s2t(target) : null;
    this.getProperty = function (property, descriptorMode, id, indexMode) {
        var propertyId = s2t(property), reference = new AR();
        reference.putProperty(s2t("property"), propertyId);
        if (id !== undefined && id !== null) {
            if (indexMode) reference.putIndex(target, id); else reference.putIdentifier(target, id);
        } else reference.putEnumerated(target, s2t("ordinal"), order ? s2t(order) : s2t("targetEnum"));
        var descriptor = executeActionGet(reference);
        return descriptorMode ? descriptor : getDescValue(descriptor, propertyId);
    };
    this.hasProperty = function (property, id, indexMode) {
        var propertyId = s2t(property), reference = new AR();
        reference.putProperty(s2t("property"), propertyId);
        if (id !== undefined && id !== null) {
            if (indexMode) reference.putIndex(target, id); else reference.putIdentifier(target, id);
        } else reference.putEnumerated(target, s2t("ordinal"), s2t("targetEnum"));
        try { return executeActionGet(reference).hasKey(propertyId); } catch (_) { return false; }
    };
    this.setProperty = function (property, value) {
        var propertyId = s2t(property), reference = new AR();
        reference.putProperty(s2t("property"), propertyId);
        reference.putEnumerated(target, s2t("ordinal"), s2t("targetEnum"));
        var descriptor = new AD();
        descriptor.putReference(s2t("null"), reference);
        descriptor.putObject(s2t("to"), propertyId, value);
        executeAction(s2t("set"), descriptor, DialogModes.NO);
    };
    this.descToObject = function (descriptor) {
        var result = {}, i;
        for (i = 0; i < descriptor.count; i++) {
            var key = descriptor.getKey(i);
            result[t2s(key)] = getDescValue(descriptor, key);
        }
        return result;
    };
    this.makeSelection = function (bounds) {
        var reference = new AR(); reference.putProperty(s2t("channel"), s2t("selection"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        var rectangle = new AD();
        rectangle.putUnitDouble(s2t("top"), s2t("pixelsUnit"), bounds.top);
        rectangle.putUnitDouble(s2t("left"), s2t("pixelsUnit"), bounds.left);
        rectangle.putUnitDouble(s2t("bottom"), s2t("pixelsUnit"), bounds.bottom);
        rectangle.putUnitDouble(s2t("right"), s2t("pixelsUnit"), bounds.right);
        descriptor.putObject(s2t("to"), s2t("rectangle"), rectangle);
        executeAction(s2t("set"), descriptor, DialogModes.NO);
    };
    this.makeSelectionFromLayer = function (channel, id) {
        var selectionRef = new AR(); selectionRef.putProperty(s2t("channel"), s2t("selection"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), selectionRef);
        var sourceRef = new AR(); sourceRef.putEnumerated(s2t("channel"), s2t("channel"), s2t(channel));
        if (id !== undefined && id !== null) sourceRef.putIdentifier(s2t("layer"), id);
        descriptor.putReference(s2t("to"), sourceRef);
        executeAction(s2t("set"), descriptor, DialogModes.NO);
    };
    this.deselect = function () {
        var reference = new AR(); reference.putProperty(s2t("channel"), s2t("selection"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        descriptor.putEnumerated(s2t("to"), s2t("ordinal"), s2t("none"));
        executeAction(s2t("set"), descriptor, DialogModes.NO);
    };
    this.quickMask = function (eventName) {
        var reference = new AR(); reference.putProperty(s2t("property"), s2t("quickMask"));
        reference.putEnumerated(s2t("document"), s2t("ordinal"), s2t("targetEnum"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        executeAction(s2t(eventName), descriptor, DialogModes.NO);
    };
    this.makeLayer = function (name) {
        var reference = new AR(); reference.putClass(s2t("layer"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        var layerDescriptor = new AD(); layerDescriptor.putString(s2t("name"), name);
        descriptor.putObject(s2t("using"), s2t("layer"), layerDescriptor);
        executeAction(s2t("make"), descriptor, DialogModes.NO);
    };
    this.makeSelectionMask = function () {
        var descriptor = new AD(); descriptor.putClass(s2t("new"), s2t("channel"));
        var reference = new AR(); reference.putEnumerated(s2t("channel"), s2t("channel"), s2t("mask"));
        descriptor.putReference(s2t("at"), reference);
        descriptor.putEnumerated(s2t("using"), s2t("userMask"), s2t("revealSelection"));
        executeAction(s2t("make"), descriptor, DialogModes.NO);
    };
    this.flatten = function () {
        executeAction(s2t("flattenImage"), undefined, DialogModes.NO);
    };
    this.mergeVisible = function () {
        try { executeAction(s2t("mergeVisible"), undefined, DialogModes.NO); } catch (_) { }
    };
    this.crop = function (deletePixels) {
        var descriptor = new AD(); descriptor.putBoolean(s2t("delete"), !!deletePixels);
        executeAction(s2t("crop"), descriptor, DialogModes.NO);
    };
    this.imageSize = function (width, height) {
        var descriptor = new AD();
        descriptor.putUnitDouble(s2t("width"), s2t("pixelsUnit"), width);
        descriptor.putUnitDouble(s2t("height"), s2t("pixelsUnit"), height);
        descriptor.putEnumerated(s2t("interpolation"), s2t("interpolationType"), s2t("automaticInterpolation"));
        executeAction(s2t("imageSize"), descriptor, DialogModes.NO);
    };
    this.saveAPNGCopy = function (file) {
        var pngOptions = new AD();
        pngOptions.putEnumerated(s2t("method"), s2t("PNGMethod"), s2t("quick"));
        pngOptions.putEnumerated(s2t("PNGInterlaceType"), s2t("PNGInterlaceType"), s2t("PNGInterlaceNone"));
        pngOptions.putEnumerated(s2t("PNGFilter"), s2t("PNGFilter"), s2t("PNGFilterAdaptive"));
        pngOptions.putInteger(s2t("compression"), 6);
        var descriptor = new AD();
        descriptor.putObject(s2t("as"), s2t("PNGFormat"), pngOptions);
        descriptor.putPath(s2t("in"), file);
        descriptor.putBoolean(s2t("copy"), true);
        executeAction(s2t("save"), descriptor, DialogModes.NO);
    };
    this.selectAllPixels = function () {
        var reference = new AR(); reference.putProperty(s2t("channel"), s2t("selection"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        descriptor.putEnumerated(s2t("to"), s2t("ordinal"), s2t("allEnum"));
        executeAction(s2t("set"), descriptor, DialogModes.NO);
    };
    this.copyPixels = function () {
        var descriptor = new AD(); descriptor.putString(s2t("copyHint"), "pixels");
        executeAction(s2t("copyEvent"), descriptor, DialogModes.NO);
    };
    this.pastePixels = function () {
        var descriptor = new AD();
        descriptor.putEnumerated(s2t("antiAlias"), s2t("antiAliasType"), s2t("antiAliasNone"));
        descriptor.putClass(s2t("as"), s2t("pixel"));
        executeAction(s2t("paste"), descriptor, DialogModes.NO);
    };
    this.invert = function () {
        executeAction(s2t("invert"), new AD(), DialogModes.NO);
    };
    this.saveACopy = function (file) {
        var jpegOptions = new AD();
        jpegOptions.putInteger(s2t("extendedQuality"), 12);
        jpegOptions.putEnumerated(s2t("matteColor"), s2t("matteColor"), s2t("none"));
        var descriptor = new AD();
        descriptor.putObject(s2t("as"), s2t("JPEG"), jpegOptions);
        descriptor.putPath(s2t("in"), file);
        descriptor.putBoolean(s2t("copy"), true);
        executeAction(s2t("save"), descriptor, DialogModes.NO);
    };
    this.place = function (file) {
        var descriptor = new AD(); descriptor.putPath(s2t("null"), file); descriptor.putBoolean(s2t("linked"), false);
        executeAction(s2t("placeEvent"), descriptor, DialogModes.NO);
    };
    this.transform = function (widthPercent, heightPercent, offsetX, offsetY) {
        var descriptor = new AD();
        descriptor.putEnumerated(s2t("freeTransformCenterState"), s2t("quadCenterState"), s2t("QCSAverage"));
        var offset = new AD();
        offset.putUnitDouble(s2t("horizontal"), s2t("pixelsUnit"), offsetX || 0);
        offset.putUnitDouble(s2t("vertical"), s2t("pixelsUnit"), offsetY || 0);
        descriptor.putObject(s2t("offset"), s2t("offset"), offset);
        descriptor.putUnitDouble(s2t("width"), s2t("percentUnit"), widthPercent);
        descriptor.putUnitDouble(s2t("height"), s2t("percentUnit"), heightPercent);
        executeAction(s2t("transform"), descriptor, DialogModes.NO);
    };
    this.rasterize = function () {
        var reference = new AR(); reference.putEnumerated(s2t("layer"), s2t("ordinal"), s2t("targetEnum"));
        var descriptor = new AD(); descriptor.putReference(s2t("target"), reference);
        executeAction(s2t("rasterizePlaced"), descriptor, DialogModes.NO);
    };
    this.selectLayersByIDs = function (ids) {
        var reference = new AR();
        for (var i = 0; i < ids.length; i++) reference.putIdentifier(s2t("layer"), ids[i]);
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        executeAction(s2t("select"), descriptor, DialogModes.NO);
    };
    this.hideSelectedLayers = function () {
        var reference = new AR(); reference.putEnumerated(s2t("layer"), s2t("ordinal"), s2t("targetEnum"));
        var list = new ActionList(); list.putReference(reference);
        var descriptor = new AD(); descriptor.putList(s2t("null"), list);
        executeAction(s2t("hide"), descriptor, DialogModes.NO);
    };
    this.setName = function (name) {
        var reference = new AR(); reference.putEnumerated(s2t("layer"), s2t("ordinal"), s2t("targetEnum"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        var layerDescriptor = new AD(); layerDescriptor.putString(s2t("name"), name);
        descriptor.putObject(s2t("to"), s2t("layer"), layerDescriptor);
        executeAction(s2t("set"), descriptor, DialogModes.NO);
    };
    this.deleteLayer = function (id) {
        var reference = new AR();
        if (id !== undefined && id !== null) reference.putIdentifier(s2t("layer"), id);
        else reference.putEnumerated(s2t("layer"), s2t("ordinal"), s2t("targetEnum"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        executeAction(s2t("delete"), descriptor, DialogModes.NO);
    };
    this.selectChannel = function (channel) {
        var reference = new AR(); reference.putEnumerated(s2t("channel"), s2t("channel"), s2t(channel));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        executeAction(s2t("select"), descriptor, DialogModes.NO);
    };
    this.selectBrush = function () {
        var reference = new AR(); reference.putClass(s2t("paintbrushTool"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        executeAction(s2t("select"), descriptor, DialogModes.NO);
    };
    this.resetSwatches = function () {
        var reference = new AR(); reference.putProperty(s2t("color"), s2t("colors"));
        var descriptor = new AD(); descriptor.putReference(s2t("null"), reference);
        executeAction(s2t("reset"), descriptor, DialogModes.NO);
    };
    this.setBrushOpacity = function (opacity) {
        var property = s2t("currentToolOptions"),
            reference = new AR(); reference.putProperty(s2t("property"), property);
        reference.putEnumerated(s2t("application"), s2t("ordinal"), s2t("targetEnum"));
        var options = executeActionGet(reference).getObjectValue(property);
        options.putInteger(s2t("opacity"), opacity);
        var toolRef = new AR(); toolRef.putClass(s2t("paintbrushTool"));
        var descriptor = new AD(); descriptor.putReference(s2t("target"), toolRef);
        descriptor.putObject(s2t("to"), s2t("target"), options);
        executeAction(s2t("set"), descriptor, DialogModes.NO);
    };
    function getDescValue(descriptor, key) {
        switch (descriptor.getType(key)) {
            case DescValueType.OBJECTTYPE: return { type: t2s(descriptor.getObjectType(key)), value: descriptor.getObjectValue(key) };
            case DescValueType.LISTTYPE: return descriptor.getList(key);
            case DescValueType.REFERENCETYPE: return descriptor.getReference(key);
            case DescValueType.BOOLEANTYPE: return descriptor.getBoolean(key);
            case DescValueType.STRINGTYPE: return descriptor.getString(key);
            case DescValueType.INTEGERTYPE: return descriptor.getInteger(key);
            case DescValueType.LARGEINTEGERTYPE: return descriptor.getLargeInteger(key);
            case DescValueType.DOUBLETYPE: return descriptor.getDouble(key);
            case DescValueType.ALIASTYPE: return descriptor.getPath(key);
            case DescValueType.CLASSTYPE: return descriptor.getClass(key);
            case DescValueType.UNITDOUBLE: return descriptor.getUnitDoubleValue(key);
            case DescValueType.ENUMERATEDTYPE: return { type: t2s(descriptor.getEnumerationType(key)), value: t2s(descriptor.getEnumerationValue(key)) };
        }
        return null;
    }
}
function Delay() {
    var settingsObj = this;
    this.getDelay = function (workflowId) {
        try { var descriptor = getCustomOptions(APP.uuid); } catch (_) { }
        if (descriptor != undefined) descriptorCodec.readInto(settingsObj, descriptor);
        if (settingsObj[workflowId]) {
            var sum = 0;
            for (var i = 0; i < settingsObj[workflowId].length; i++) sum += settingsObj[workflowId][i];
            sum = Math.round(sum / settingsObj[workflowId].length);
            return sum < 1000 ? 1000 : sum;
        }
        return 7500;
    };
    this.saveDelay = function (workflowId, delay) {
        if (!workflowId) return;
        delay = Math.max(1, Math.round(Number(delay) || 0));
        if (!(settingsObj[workflowId] instanceof Array)) settingsObj[workflowId] = [];
        if (settingsObj[workflowId].length >= 3)
            settingsObj[workflowId].splice(0, settingsObj[workflowId].length - 2);
        settingsObj[workflowId].push(delay);
        putCustomOptions(APP.uuid, descriptorCodec.toDescriptor(settingsObj, true));
    };
}
function Locale() {
    this.all = { ru: "Все", en: "All" };
    this.recordSettingsToAction = { ru: "Записывать настройки в экшен", en: "Record settings to action" };
    this.automatic = { ru: "Автоматически", en: "Automatic" };
    this.autoResize = { ru: "Автомасштаб", en: "Auto resize" };
    this.brushSettings = { ru: "Настройки кисти", en: "Brush settings" };
    this.browse = { ru: "Обзор…", en: "Browse…" };
    this.connectionSettings = { ru: "Подключение", en: "Connection" };
    this.errorDialogTitle = { ru: "Ошибка", en: "Error" };
    this.errSettingsSaveAfterError = { ru: "Операция завершилась с ошибкой, и настройки сохранить не удалось:", en: "The operation failed and the settings could not be saved:" };
    this.errorOccurred = { ru: "Произошла ошибка", en: "An error occurred" };
    this.errorDialogIntro = { ru: "Операция не завершена. Технические подробности:", en: "The operation was not completed. Technical details:" };
    this.errorDetails = { ru: "Подробности ошибки", en: "Error details" };
    this.errApiConnection = { ru: "Нет соединения с Python API.", en: "Cannot connect to Python API." };
    this.errApiTimeout = { ru: "Превышено время ожидания ответа Python API. Лог: %LOCALAPPDATA%\\" + APP.tempFolder + "\\" + API_FILE + ".log", en: "Python API response timed out. Log: %LOCALAPPDATA%\\" + APP.tempFolder + "\\" + API_FILE + ".log" };
    this.errApiInvalidAnswer = { ru: "Python API вернул повреждённый ответ.", en: "Python API returned an invalid response." };
    this.errApiProtocolA = { ru: "Запущена несовместимая версия протокола Python API (", en: "An incompatible Python API protocol is running (" };
    this.errApiProtocolB = { ru: "). Ожидается версия ", en: "). Expected protocol: " };
    this.errEmptyApiAnswer = { ru: "Пустой ответ Python API.", en: "Empty response from Python API." };
    this.errListenerPort = { ru: "Не удалось открыть listener-порт ", en: "Cannot open listener port " };
    this.errMode = { ru: APP.name + " работает только с RGB-документами.", en: APP.name + " works only with RGB documents." };
    this.errNoResult = { ru: "Бэкенд не вернул результат.", en: "The backend returned no result." };
    this.errPlacedBounds = { ru: "Не удалось определить размер вставленного слоя.", en: "Could not determine placed layer bounds." };
    this.errPythonMissingA = { ru: "Не найден ", en: "Could not find " };
    this.errPythonMissingB = { ru: ".pyw или .py рядом с JSX либо в подпапке lib.", en: ".pyw or .py next to JSX or in the lib subfolder." };
    this.errPythonStartA = { ru: "Python API не запустился на ", en: "Python API did not start on " };
    this.errPythonStartB = ".";
    this.errResultFile = { ru: "Файл результата не найден:", en: "Result file not found:" };
    this.errSaveJpeg = { ru: "Photoshop не смог сохранить временный JPEG.", en: "Photoshop could not save the temporary JPEG." };
    this.errSavePng = { ru: "Photoshop не смог сохранить временный PNG с маской.", en: "Photoshop could not save the temporary PNG with a mask." };
    this.errSaveMask = { ru: "Photoshop не смог сохранить временную маску PNG.", en: "Photoshop could not save the temporary PNG mask." };
    this.errInpaintMaskMissing = { ru: "Для этого workflow не настроен вход маски. Откройте настройки workflow и выберите MASK основной ноды LoadImage или LoadImageMask.", en: "No mask input is configured for this workflow. Open workflow settings and select the main LoadImage MASK or a LoadImageMask node." };
    this.errInpaintInputDisconnected = { ru: "Workflow не использует MASK основной ноды LoadImage. Подключите выход MASK к inpaint-ветке или выберите LoadImageMask в настройках workflow.", en: "The workflow does not use the main LoadImage MASK output. Connect MASK to the inpaint branch or select a LoadImageMask node in workflow settings." };
    this.errInpaintNodeDisconnected = { ru: "Выбранная нода LoadImageMask не подключена к workflow. Подключите её выход MASK к inpaint-ветке.", en: "The selected LoadImageMask node is not connected to the workflow. Connect its MASK output to the inpaint branch." };
    this.errSelectedWorkflowMissing = { ru: "Выбранный workflow больше не найден.", en: "The selected workflow can no longer be found." };
    this.errWorkflowInvalid = { ru: "Workflow не прошёл проверку. Откройте ⚙ или добавьте метки к названиям нод.", en: "Workflow validation failed. Open ⚙ or add tags to node titles." };
    this.cfgScale = "CFG Scale";
    this.generate = { ru: "Генерировать", en: "Generate" };
    this.guidance = "Guidance";
    this.generationTimeout = { ru: "Таймаут генерации, с:", en: "Generation timeout, s:" };
    this.generationWarnings = { ru: "Генерация завершена, но некоторые параметры не были применены:", en: "Generation completed, but some parameters were not applied:" };
    this.historyCheckSelection = { ru: "Проверить выделение", en: "Check selection" };
    this.historyPlaceResult = { ru: "Вставить результат генерации", en: "Place generated result" };
    this.historyPrepareSelection = { ru: "Подготовить выделение", en: "Prepare selection" };
    this.inputImage = { ru: "Входное изображение", en: "Input image" };
    this.inpaintMask = { ru: "Маска inpaint", en: "Inpaint mask" };
    this.imageReference = { ru: "Референс", en: "Reference image" };
    this.imageStitchInputs = "ImageStitch inputs";
    this.imageStitchInput = "ImageStitch input";
    this.referenceInputs = { ru: "Входы референсов", en: "Reference inputs" };
    this.referenceInputsHelp = { ru: "Выберите LoadImage-ноды, которые должны получать отдельные файлы-референсы. Основной вход Photoshop здесь выбирать не нужно.", en: "Select LoadImage nodes that should receive separate reference files. Do not select the main Photoshop input here." };
    this.noneReference = { ru: "нет", en: "none" };
    this.selectReferenceImage = { ru: "Выберите референсное изображение", en: "Select reference image" };
    this.saveChanges = { ru: "Сохранить изменения", en: "Save changes" };
    this.infoEmptyWorkflowFolder = { ru: "В выбранной папке нет API-workflow (*.json). Откройте настройки, чтобы выбрать другую папку.", en: "The selected folder contains no API workflows (*.json). Open Settings to choose another folder." };
    this.infoMissingWorkflowFolder = { ru: "Папка API-workflow ComfyUI не выбрана или не найдена. Нажмите ⚙ и выберите папку с API-workflow.", en: "The ComfyUI API-workflow folder is not selected or cannot be found. Click ⚙ and select the API-workflow folder." };
    this.jsxLine = { ru: "Строка JSX: ", en: "JSX line: " };
    this.layerMetadata = { ru: "Сохранять настройки в метаданных слоя", en: "Store settings in layer metadata" };
    this.loadLayerMetadata = { ru: "Загрузить параметры из метаданных активного слоя", en: "Load settings from active layer metadata" };
    this.errLayerMetadata = { ru: "Не удалось сопоставить workflow из метаданных слоя.", en: "Could not match the workflow stored in layer metadata." };
    this.maximumMp = { ru: "Макс. МП:", en: "Max MP:" };
    this.minimumSide = { ru: "Мин. сторона:", en: "Min. side:" };
    this.resizePreset = { ru: "Профиль автомасштаба", en: "Auto-resize profile" };
    this.resizePresetManagement = { ru: "Профили автомасштаба", en: "Auto-resize profiles" };
    this.resizePresetNew = { ru: "Новый профиль", en: "New profile" };
    this.resizePresetTitle = { ru: "Профиль автомасштаба", en: "Auto-resize profile" };
    this.resizePresetPrompt = { ru: "Укажите имя профиля автомасштаба", en: "Enter an auto-resize profile name" };
    this.resizeMinShort = { ru: "мин", en: "min" };
    this.resizeMaxShort = { ru: "макс", en: "max" };
    this.presetCopy = { ru: " копия", en: " copy" };
    this.errResizePreset = { ru: "Профиль «%1» уже существует. Перезаписать?", en: "Profile “%1” already exists. Overwrite?" };
    this.negativePrompt = "Negative prompt";
    this.lora = "LoRA";
    this.selectLora = { ru: "Выберите LoRA", en: "Select LoRA" };
    this.loraSearch = { ru: "Фильтр списка LoRA", en: "Filter the LoRA list" };
    this.nodeInput = { ru: "Нода #", en: "Node #" };
    this.none = { ru: "Снять все", en: "Select none" };
    this.opacity = { ru: "Непрозрачность кисти", en: "Brush opacity" };
    this.imageSettings = { ru: "Параметры изображения", en: "Image settings" };
    this.outputImage = { ru: "Выходное изображение", en: "Output image" };
    this.comfyPort = { ru: "Порт ComfyUI:", en: "ComfyUI port:" };
    this.presetRefreshButton = "↻";
    this.presetAddButton = "+";
    this.presetSaveButton = "✔";
    this.presetDeleteButton = "×";
    this.presetNew = { ru: "Новый пресет", en: "New preset" };
    this.errDefaultPreset = { ru: "Используйте другое имя для пресета.", en: "Use a different preset name." };
    this.errPreset = { ru: "Пресет «%1» уже существует. Перезаписать?", en: "Preset “%1” already exists. Overwrite?" };
    this.presetAdd = { ru: "Добавить пресет", en: "Add preset" };
    this.presetDefault = { ru: "по умолчанию", en: "default" };
    this.presetDelete = { ru: "Удалить пресет", en: "Delete preset" };
    this.presetDeleteConfirmA = { ru: "Удалить пресет «", en: "Delete preset ‘" };
    this.presetDeleteConfirmB = { ru: "»?", en: "’?" };
    this.presetNamePrompt = { ru: "Укажите имя пресета", en: "Enter preset name" };
    this.presetRestore = { ru: "Восстановить значения пресета", en: "Restore preset values" };
    this.promptClear = { ru: "Очистить поле", en: "Clear field" };
    this.presetSave = { ru: "Сохранить пресет", en: "Save preset" };
    this.translate = { ru: "Перевести", en: "Translate" };
    this.translatePromptHelp = { ru: "Перевести текущий промпт на английский", en: "Translate the current prompt into English" };
    this.progressTranslate = { ru: "Перевод промпта", en: "Translating prompt" };
    this.errTranslate = { ru: "Не удалось перевести промпт.", en: "Could not translate the prompt." };
    this.prompt = "Prompt";
    this.primarySize = { ru: "Основной размер", en: "Primary size" };
    this.generationProgressTitle = { ru: "Генерация изображения", en: "Image generation" };
    this.progressAnalyze = { ru: "Анализ workflow", en: "Analyzing workflow" };
    this.progressGenerate = { ru: "Генерация изображения… ", en: "Generating image… " };
    this.progressPrepare = { ru: "Инициализация модели… ", en: "Initializing model… " };
    this.progressInitializeAction = { ru: "инициализация", en: "initializing" };
    this.progressGenerateAction = { ru: "генерация изображения", en: "generating image" };
    this.progressStartPython = { ru: "Запуск Python-сервера…", en: "Starting Python server…" };
    this.progressInitializing = { ru: "Инициализация " + APP.name + "… ", en: "Initializing " + APP.name + "… " };
    this.progressHandshake = { ru: "Подключение к Python API…", en: "Connecting to Python API…" };
    this.progressWorkflows = { ru: "Загрузка списка workflow…", en: "Loading workflow list…" };
    this.progressReady = { ru: "Подготовка интерфейса завершена", en: "Interface data is ready" };
    this.flatten = { ru: "Объединять слои перед генерацией", en: "Flatten layers before generation" };
    this.keepAspectRatioDuringPlace = { ru: "Сохранять пропорции при размещении", en: "Keep aspect ratio during place" };
    this.rasterize = { ru: "Растеризовать сгенерированное изображение", en: "Rasterize generated image" };
    this.sampler = "Sampling method";
    this.scheduler = "Schedule type";
    this.seed = "Seed";
    this.randomSeed = { ru: "Установить случайный seed", en: "Set a random seed" };
    this.recommended = { ru: "Рекомендуемые", en: "Recommended" };
    this.refreshWorkflows = { ru: "Обновить список JSON", en: "Refresh JSON list" };
    this.reinitializeWorkflow = { ru: "Повторно проанализировать workflow", en: "Reanalyze workflow" };
    this.resize = "Resize";
    this.selectBrush = { ru: "Активировать кисть после генерации", en: "Select brush after generation" };
    this.selection = { ru: "Выделение: ", en: "Selection: " };
    this.selectWorkflowFolder = { ru: "Выберите папку с workflow, сохранёнными через Export Workflow (API)", en: "Select the folder containing workflows exported with Export Workflow (API)" };
    this.selectForgeSchemaFolder = { ru: "Выберите папку с JSON-схемами Forge", en: "Select the folder containing Forge JSON schemas" };
    this.scriptSettings = { ru: "Настройки скрипта", en: "Script settings" };
    this.sizeFromInput = { ru: "В workflow нет width/height: итоговый размер задаётся загруженным JPEG.", en: "The workflow has no width/height: size is defined by the uploaded JPEG." };
    this.sizeMultiple = { ru: "Кратность width/height:", en: "Width/height multiple:" };
    this.sizeWorkflowBinding = { ru: "Размер будет записан в обнаруженные поля workflow.", en: "Size will be written to the detected workflow fields." };
    this.secondsShort = { ru: "с", en: "s" };
    this.steps = "Sampling steps";
    this.denoisingStrength = "Denoising strength";
    this.visibleParameters = { ru: "Параметры главного окна", en: "Main-window parameters" };
    this.workflowFolder = { ru: "Папка API-workflow:", en: "API workflow folder:" };
    this.forgeSchemaFolder = { ru: "Папка схем Forge:", en: "Forge schema folder:" };
    this.workflow = "Workflow";
    this.workflowSettings = { ru: "Настройки workflow", en: "Workflow settings" };
    this.forgeSchemaSettings = { ru: "Настройки схемы Forge", en: "Forge schema settings" };
    this.forgeSchemaSettingsNote = { ru: "Выберите поля главного окна. Скрытые поля используют значения по умолчанию из JSON-схемы. Модель и VAE / Text encoders всегда видимы.", en: "Choose the main-window fields. Hidden fields use defaults from the JSON schema. Model and VAE / Text encoders are always visible." };
    this.alwaysVisible = { ru: "всегда видно", en: "always visible" };
    this.backendLabel = { ru: "Бэкенд", en: "Backend" };
    this.host = { ru: "IP / хост:", en: "IP / host:" };
    this.forgePort = { ru: "Порт Forge Neo:", en: "Forge Neo port:" };
    this.uiPreset = "UI Preset";
    this.modules = "VAE / Text encoders";
    this.distilledCfgScale = "Distilled CFG Scale";
    this.shift = "Shift";
    this.refreshForgeCatalog = { ru: "Обновить текущую схему и её данные", en: "Refresh current schema and its data" };
    this.reloadForgeSchemas = { ru: "Обновить список схем и текущую схему", en: "Refresh schema list and current schema" };
    this.progressForgeCatalog = { ru: "Загрузка данных Forge Neo…", en: "Loading Forge Neo data…" };
    this.progressForgePresets = { ru: "Загрузка схем Forge…", en: "Loading Forge schemas…" };
    this.infoEmptyForgePresets = { ru: "В выбранной папке нет подходящих JSON-схем Forge. Откройте настройки, чтобы выбрать другую папку.", en: "The selected folder contains no compatible Forge JSON schemas. Open Settings to choose another folder." };
    this.infoMissingForgeSchemaFolder = { ru: "Папка JSON-схем Forge не выбрана или не найдена. Нажмите ⚙ и выберите папку со схемами.", en: "The Forge JSON schema folder is not selected or cannot be found. Click ⚙ and select the schema folder." };
    this.detectedBackends = { ru: "Доступные бэкенды:", en: "Available backends:" };
    this.detectBackends = { ru: "Найти запущенные бэкенды", en: "Detect running backends" };
    this.backendsNone = { ru: "не найдены", en: "none detected" };
    this.errNoBackendAvailable = { ru: "Не найден запущенный ComfyUI или Forge Neo. Запустите хотя бы одну оболочку и повторите запуск скрипта.", en: "No running ComfyUI or Forge Neo instance was detected. Start at least one backend and run the script again." };
    this.errBackendUnavailable = { ru: "Выбранный бэкенд сейчас недоступен.", en: "The selected backend is currently unavailable." };
    this.workflowTagNote = {
        ru: "Метки можно дописать прямо к заголовкам нод в ComfyUI: #PS-INPUT, #PS-OUTPUT, #PS-SIZE, #PS-MAIN, #PS-REF, #PS-MASK и #PS-UI. После переименования снова выполните Export Workflow (API). Ручное редактирование JSON не требуется.",
        en: "Append tags directly to node titles in ComfyUI: #PS-INPUT, #PS-OUTPUT, #PS-SIZE, #PS-MAIN, #PS-REF, #PS-MASK and #PS-UI. Export Workflow (API) again after renaming. Manual JSON editing is not required."
    };
}
function calculateSizeFromScale(width, height, scale, multiple) {
    multiple = Math.max(1, parseInt(multiple, 10) || 1);
    scale = Math.max(0.01, Number(scale) || 1);
    var targetWidth = scale != 1 ? Math.floor(width * scale / multiple) * multiple : width,
        targetHeight = scale != 1 ? Math.floor(height * scale / multiple) * multiple : height;
    return {
        width: targetWidth || multiple,
        height: targetHeight || multiple
    };
}
function autoScale(bounds, preset) {
    preset = preset || presets.findResize("", cfg.resizePresets);
    var shortSide = Math.min(bounds.width, bounds.height),
        pixels = bounds.width * bounds.height,
        maxPixels = preset.maxMp * 1000000,
        scale = 1,
        limitedByMaxArea = false;
    if (shortSide < preset.minSide) scale = preset.minSide / shortSide;
    if (pixels * scale * scale > maxPixels) {
        scale = Math.sqrt(maxPixels / pixels);
        limitedByMaxArea = true;
    }
    scale = limitedByMaxArea ? Math.floor(scale * 1000000) / 1000000 : Math.ceil(scale * 1000000) / 1000000;
    if (scale > 4) scale = 4;
    return scale > 0 ? scale : 0.000001;
}
function Presets() {
    var self = this,
        protectedResizeNames = ["SD", "SDXL", "FLUX/QWEN"],
        promptDefaults = {
            positive: {},
            negative: {
                "SD": "(deformed, distorted, disfigured:1.3), poorly drawn, bad anatomy, wrong anatomy, extra limb, missing limb, floating limbs, (mutated hands and fingers:1.4), disconnected limbs, mutation, mutated, ugly, disgusting, blurry, amputation",
                "Realistic": "(deformed iris, deformed pupils, semi-realistic, cgi, 3d, render, sketch, cartoon, drawing, anime), text, cropped, out of frame, worst quality, low quality, jpeg artifacts, ugly, duplicate, morbid, mutilated, extra fingers, mutated hands, poorly drawn hands, poorly drawn face, mutation, deformed, blurry, dehydrated, bad anatomy, bad proportions, extra limbs, cloned face, disfigured, gross proportions, malformed limbs, missing arms, missing legs, extra arms, extra legs, fused fingers, too many fingers, long neck"
            }
        };
    function extractLoraTags(text) {
        var matches = String(text || "").match(/<lora:[^<>]+>/gi), result = [];
        if (!matches) return result;
        for (var i = 0; i < matches.length; i++)
            if (!arrayContainsCaseInsensitive(result, matches[i])) result.push(matches[i]);
        return result;
    }
    this.defaultPrompt = function () { return cloneObject(promptDefaults); };
    this.promptStore = function (config, context) {
        context = context == "negative" ? "negative" : "positive";
        if (!config.promptPresets || typeof config.promptPresets != "object")
            config.promptPresets = config.data.promptPresets = self.defaultPrompt();
        if (!config.promptPresets[context] || typeof config.promptPresets[context] != "object")
            config.promptPresets[context] = {};
        config.data.promptPresets = config.promptPresets;
        return config.promptPresets[context];
    };
    this.promptText = function (context, text) {
        text = String(text || "");
        if (context != "positive") return text;
        return text.replace(/<lora:[^<>]+>/gi, " ")
            .replace(/[ \t]+/g, " ")
            .replace(/^[ \t]+|[ \t]+$/gm, "")
            .replace(/^\s+|\s+$/g, "");
    };
    this.applyPrompt = function (context, currentText, presetText) {
        presetText = String(presetText || "");
        if (context != "positive") return presetText;
        var loras = extractLoraTags(currentText), result = presetText;
        for (var i = loras.length - 1; i >= 0; i--)
            if (String(result).toLowerCase().indexOf(String(loras[i]).toLowerCase()) < 0)
                result = loras[i] + (result ? " " : "") + result;
        return result;
    };
    this.createResize = function (name, minSide, maxMp) {
        return { name: name, minSide: minSide, maxMp: maxMp };
    };
    this.defaultResize = function () {
        return [
            self.createResize("SD", 512, 1.5),
            self.createResize("SDXL", 640, 2),
            self.createResize("FLUX/QWEN", 512, 2),
            self.createResize("HiRes", 1024, 4)
        ];
    };
    this.findResizeIndex = function (name, list) {
        if (typeof name != "string") return -1;
        name = name.toUpperCase();
        for (var i = 0; i < list.length; i++) if (String(list[i].name).toUpperCase() == name) return i;
        return -1;
    };
    this.findResize = function (name, list) {
        list = list && list.length ? list : self.defaultResize();
        var index = self.findResizeIndex(name, list);
        return index >= 0 ? list[index] : list[0];
    };
    this.normalizeResizeName = function (name, list) {
        var preset = self.findResize(name, list);
        return preset ? preset.name : "";
    };
    this.formatResize = function (preset) {
        return preset.name + " (" + localize(str.resizeMinShort) + " " + preset.minSide + " px, " + localize(str.resizeMaxShort) + " " + preset.maxMp + " MP)";
    };
    this.isProtectedResize = function (name) {
        name = String(name || "").toUpperCase();
        for (var i = 0; i < protectedResizeNames.length; i++)
            if (name == String(protectedResizeNames[i]).toUpperCase()) return true;
        return false;
    };
}
function createRequestId() {
    return "unified_" + new Date().getTime() + "_" + Math.floor(Math.random() * 1000000000);
}
function clamp(value, minimum, maximum) { return Math.max(minimum, Math.min(maximum, value)); }
function roundTo(value, digits) { var k = Math.pow(10, digits || 0); return Math.round(value * k) / k; }
function roundByStep(value, step, origin) { return Math.round((value - origin) / step) * step + origin; }
function numberPrecision(value) {
    var text = String(Math.abs(Number(value))),
        exponentIndex = text.toLowerCase().indexOf("e-");
    if (exponentIndex >= 0) {
        var mantissa = text.substring(0, exponentIndex),
            exponent = parseInt(text.substring(exponentIndex + 2), 10) || 0,
            point = mantissa.indexOf(".");
        return Math.min(6, exponent + (point < 0 ? 0 : mantissa.length - point - 1));
    }
    var decimalPoint = text.indexOf(".");
    return Math.min(6, decimalPoint < 0 ? 0 : text.length - decimalPoint - 1);
}
function formatNumber(value, integer, precision) {
    return integer ? String(Math.round(value)) : Number(value).toFixed(precision === undefined ? 2 : precision);
}
function arrayContains(array, value) { if (!array) return false; for (var i = 0; i < array.length; i++) if (array[i] == value) return true; return false; }
function normalizedBindingOverrides(value) {
    value = value && typeof value == "object" ? value : {};
    var references = value.references instanceof Array ? value.references.slice(0) : [], i;
    for (i = 0; i < references.length; i++) references[i] = String(references[i] || "");
    references.sort();
    return {
        input: String(value.input || ""),
        mask: String(value.mask || ""),
        references: references,
        output: String(value.output || ""),
        size: String(value.size || "")
    };
}
function bindingOverridesEqual(first, second) {
    return jsonStringify(normalizedBindingOverrides(first)) == jsonStringify(normalizedBindingOverrides(second));
}
function startsWithSemantic(controlId, semantic) { return controlId == semantic || controlId.indexOf(semantic + "__") === 0; }
function cloneObject(source) {
    if (source === null || source === undefined || typeof source != "object") return source;
    var result = source instanceof Array ? [] : {}, key;
    for (key in source) if (source.hasOwnProperty(key)) result[key] = cloneObject(source[key]);
    return result;
}
function jsonStringify(value) {
    if (value === null || value === undefined) return "null";
    var type = typeof value;
    if (type == "string") return "\"" + escapeJsonString(value) + "\"";
    if (type == "number") return isFinite(value) ? String(value) : "null";
    if (type == "boolean") return value ? "true" : "false";
    if (value instanceof Array) {
        var array = [];
        for (var i = 0; i < value.length; i++) array.push(jsonStringify(value[i]));
        return "[" + array.join(",") + "]";
    }
    if (type == "object") {
        var fields = [], key;
        for (key in value) if (value.hasOwnProperty(key) && typeof value[key] != "function")
            fields.push("\"" + escapeJsonString(key) + "\":" + jsonStringify(value[key]));
        return "{" + fields.join(",") + "}";
    }
    return "null";
}
function escapeJsonString(value) {
    return String(value)
        .replace(/\\/g, "\\\\")
        .replace(/\"/g, "\\\"")
        .replace(/\r/g, "\\r")
        .replace(/\n/g, "\\n")
        .replace(/\t/g, "\\t")
        .replace(/[\x00-\x1f]/g, function (character) {
            var code = character.charCodeAt(0).toString(16);
            while (code.length < 4) code = "0" + code;
            return "\\u" + code;
        });
}
function jsonParse(text) {
    if (text === null || text === undefined || text === "") return null;
    return eval("(" + text + ")");
}