#target photoshop
/*
// BEGIN__HARVEST_EXCEPTION_ZSTRING
<javascriptresource>
<name>img2img helper (use SHIFT to view dialog)</name>
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
$.localize = true; // ScriptUI/Photoshop автоматически локализует объекты Locale.
//$.locale = 'ru'
var APP = {
	name: "img2img helper",
	uuid: "5f6f57dc-80c8-49b4-9ea9-405d132b7b30",
	settingsFile: "img2img helper.desc",
	tempFolder: "img2img helper",
	comfyUploadSubfolder: "img2img-helper",
	startupFile: "state/startup.json",
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
		comfyAnalysisUuid: "fbb6e4b0-5c1a-4d42-9a87-63e2f449ce18",
		maxWorkflowSchemas: 6
	}
},
	VER = "0.203",
	// true всегда открывает окно и отключает распознавание Actions.
	DEBUG_FIRST_LAUNCH_WITH_INTERFACE = false,
	API_FILE = "img2img-api",
	API_HOST = "127.0.0.1",
	API_PORT_SEND = 6370,
	API_PORT_LISTEN = 6371,
	API_PROTOCOL = 3,
	// На запуск Python и установку зависимостей даётся две минуты.
	START_TIMEOUT = 2 * 60 * 1000,
	SHORT_TIMEOUT = 8000,
	STARTUP_PROGRESS_DELAY = 1000,
	TRANSLATE_TIMEOUT = 10 * 60 * 1000,
	ANALYZE_TIMEOUT = 90000,
	// За 7,5 с подготовка проходит половину пути к пределу 20%.
	GENERATION_PREPARE_EXPECTED_MS = 7500,
	GENERATION_RUN_DEFAULT_EXPECTED_MS = 7500,
	GENERATION_PREPARE_SEGMENT = 20,
	GENERATION_RUN_SEGMENT = 80,
	GENERATION_TOTAL_SEGMENTS = 100,
	// doProgressTask() получает долю оставшегося участка.
	PROGRESS_STAGE_TARGET = 0.95,
	API_POLL_INTERVAL = 25,
	API_POLL_SLEEP = 5,
	// Вертикальный ритм главного окна. Эти значения можно менять независимо:
	// обычные динамические блоки, верхняя строка Selection/Settings, Backend и Workflow/Schema.
	MAIN_UI_BLOCK_SPACING = 5,
	MAIN_UI_SELECTION_HEADER_BOTTOM_MARGIN = 0,
	MAIN_UI_BACKEND_TOP_MARGIN = 5,
	MAIN_UI_BACKEND_BOTTOM_MARGIN = 0,
	MAIN_UI_SELECTOR_TOP_MARGIN = 5,
	MAIN_UI_SELECTOR_BOTTOM_MARGIN = 5,
	DESC_PROFILE_CLEANUP_INTERVAL = 500,
	BACKEND_COMFY = "comfy",
	BACKEND_FORGE = "forge",
	TRANSFORM_STRETCH = "stretch",
	TRANSFORM_PROPORTIONAL = "proportional",
	REFERENCE_IMAGE_FILTER = "JPEG/PNG/WebP:*.jpg;*.jpeg;*.png;*.webp",
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
	messages = new MessageCenter(),
	generationTimings = new Delay(),
	str = new Locale(),
	apl = new AM("application"),
	doc = new AM("document"),
	lr = new AM("layer"),
	layerMetadata = new LayerMetadata(),
	initialState = null,
	generationResultPlaced = false,
	startupProgress = null,
	isCancelled = false,
	actionPlaybackMode = false,
	actionUsesRecordedSettings = false,
	globalSettings = null,
	settingsReady = false,
	keyboardState = ScriptUI.environment.keyboardState;
// ТОЧКА ВХОДА: Shift открывает окно только для текущего запуска.
try { init(); }
catch (e) {
	if (startupProgress) { try { startupProgress.close(); } catch (_) { } startupProgress = null; }
	if (isUserCancellation(e)) {
		isCancelled = true;
	} else {
		// После placeResult не повторяем сохранение при ошибке финализации.
		var settingsSaveError = generationResultPlaced ? "" : action.saveAfterError(),
			errorText = errorMessageText(e) +
				(e.line ? "\n\n" + str.jsxLine + e.line : "");
		if (settingsSaveError) errorText += "\n\n" + str.errSettingsSaveAfterError +
			"\n" + settingsSaveError;
		messages.error(errorText, APP.name);
		isCancelled = false;
		$.setenv(APP.dialogEnvKey, "true");
	}
}
finally {
	// При неудаче возвращаем состояние документа до checkSelection().
	restoreInitialDocumentState();
}
isCancelled ? "cancel" : undefined;
function restoreInitialDocumentState() {
	if (generationResultPlaced || !initialState || !app.documents.length) return;
	try { app.activeDocument.activeHistoryState = initialState; }
	catch (_) { }
}
// Загружает настройки из DESC или Action, проверяет выделение и backend,
// затем либо открывает интерфейс, либо запускает генерацию в тихом режиме.
function init() {
	if (!app.documents.length) return;
	initialState = app.activeDocument.activeHistoryState;
	if (doc.getProperty("mode").value != "RGBColor") throw new Error(str.errMode);
	var playbackCount = action.getPlaybackParameterCount(),
		settingsWarnings = [];
	actionPlaybackMode = action.isPlayback();
	var forceDialog = keyboardState.shiftKey || (!actionPlaybackMode && action.hasInterfaceArgument());
	if (actionPlaybackMode) {
		var actionSettingsMode = action.getRecordedSettingsMode();
		if (actionSettingsMode === false) {
			cfg.load();
			settingsWarnings = settingsWarnings.concat(cfg.consumeLoadWarnings());
			cfg.recordSettingsToAction = cfg.data.recordSettingsToAction = false;
			actionUsesRecordedSettings = false;
		} else {
			cfg.loadFromAction();
			actionUsesRecordedSettings = true;
			globalSettings = new Config();
			globalSettings.load();
			settingsWarnings = settingsWarnings.concat(globalSettings.consumeLoadWarnings());
			cfg.copySharedLibrariesFrom(globalSettings);
			cfg.copyPythonRuntimeSettingsFrom(globalSettings);
		}
	} else {
		cfg.load();
		settingsWarnings = settingsWarnings.concat(cfg.consumeLoadWarnings());
		// Внешний запуск Photoshop может передать один playback-параметр, не являясь Action.
		// Такой запуск всегда должен открыть интерфейс, даже если прошлый запуск был тихим.
		if (playbackCount == 1) $.setenv(APP.dialogEnvKey, "true");
	}
	settingsReady = true;
	cfg.cleanReferenceHistory();
	var environmentMode = DEBUG_FIRST_LAUNCH_WITH_INTERFACE ? null : $.getenv(APP.dialogEnvKey),
		// После ошибки dialog-флаг открывает следующий запуск также внутри Action.
		// Пользовательская отмена не изменяет сохранённый режим окна.
		showInterface = DEBUG_FIRST_LAUNCH_WITH_INTERFACE || (actionPlaybackMode
			? forceDialog || environmentMode == "true" || app.playbackDisplayDialogs == DialogModes.ALL
			: forceDialog || environmentMode == "true" || environmentMode == null);
	var selection = {
		result: false,
		bounds: null,
		sourceBounds: null,
		previousGeneration: null,
		junk: null,
		inpaint: false
	};
	app.activeDocument.suspendHistory(localize(str.historyCheckSelection), "checkSelection(selection)");
	if (!selection.result) return;
	try {
		// TCP-check определяет, нужен ли прогресс запуска.
		var apiRunning = api.isRunning();
		if (!apiRunning) {
			startupProgress = ui.createStartupProgress(str.progressStartPython, START_TIMEOUT + ANALYZE_TIMEOUT);
			startupProgress.show();
		}
		api.initialize(startupProgress, apiRunning);
		if (startupProgress) startupProgress.setStage(str.progressHandshake, 22);
		// Обычное открытие UI использует снимок фонового BackendMonitor и не
		// блокируется на сетевом probe. Silent mode и Action по-прежнему
		// синхронно проверяют выбранный backend перед продолжением.
		var startupVerifyBackend = showInterface && !actionPlaybackMode ? "" : cfg.activeBackend;
		backend.applyStatus(api.handshake(
			startupProgress, null, showInterface ? "cached" : "silent",
			startupVerifyBackend
		));
		if (showInterface && !backend.isAvailable(cfg.activeBackend) &&
			!(actionPlaybackMode && actionUsesRecordedSettings)) {
			// Отрицательный cached-status перепроверяем живым probe: backend мог
			// быть запущен уже после последнего прохода фонового monitor.
			backend.applyStatus(api.handshake(
				startupProgress, null, "cached", cfg.activeBackend
			));
			if (!backend.isAvailable(cfg.activeBackend)) {
				var alternateBackend = cfg.activeBackend == BACKEND_FORGE ? BACKEND_COMFY : BACKEND_FORGE;
				backend.applyStatus(api.handshake(startupProgress, null, "cached", alternateBackend));
			}
		}
		var backendChangedAtStartup = false;
		// Тихий запуск и Action не подменяют выбранный backend.
		if (showInterface) {
			if (actionPlaybackMode && actionUsesRecordedSettings) {
				if (!backend.isAvailable(cfg.activeBackend)) throw new Error(str.errBackendUnavailable);
			} else {
				backendChangedAtStartup = backend.normalizeActiveBackend();
				if (!backend.hasAvailable()) throw new Error(str.errNoBackendAvailable);
			}
		} else if (!backend.isAvailable(cfg.activeBackend)) throw new Error(str.errBackendUnavailable);
		// Видимый UI использует сохранённый каталог, если выбранный элемент ещё
		// присутствует в нём. Изменение выбранного JSON проверяется по stamp, а
		// полный поиск новых файлов выполняется при первом запуске и по кнопке ↻.
		var interfaceRequestedAtStartup = showInterface,
			hasCachedForgeSelection = showInterface && cfg.activeBackend == BACKEND_FORGE &&
				cfg.forgeCatalog instanceof Array &&
				!!backend.findForgeSchema(cfg.forgeCatalog, cfg.selectedForgePreset),
			hasCachedComfySelection = showInterface && cfg.activeBackend == BACKEND_COMFY &&
				cfg.workflowCatalog instanceof Array &&
				!!backend.findWorkflow(cfg.workflowCatalog, cfg.selectedWorkflow),
			allowFastInitialLoad = !backendChangedAtStartup && !settingsWarnings.length &&
				(!showInterface || hasCachedForgeSelection || hasCachedComfySelection),
			initial = backend.loadInitialData(startupProgress, allowFastInitialLoad);
		initial.notices = settingsWarnings.concat(initial.notices instanceof Array ? initial.notices : []);
		if (backendChangedAtStartup || initial.forceDialog || initial.notices.length ||
			(initial.emptyDropdownIds instanceof Array && initial.emptyDropdownIds.length)) {
			showInterface = true;
			$.setenv(APP.dialogEnvKey, "true");
		}
		// Если тихий запуск сам потребовал UI, загружаем полный список.
		// Для UI, запрошенного пользователем изначально, проверенного cached-list достаточно.
		if (showInterface && initial.fastPath && !interfaceRequestedAtStartup) {
			var fastNotices = initial.notices instanceof Array ? initial.notices : [],
				fullInitial = backend.loadInitialData(startupProgress, false),
				fullNotices = fullInitial.notices instanceof Array ? fullInitial.notices : [],
				seenNotices = {}, mergedNotices = [], notice, noticeKey, ni;
			for (ni = 0; ni < fastNotices.length + fullNotices.length; ni++) {
				notice = ni < fastNotices.length ? fastNotices[ni] : fullNotices[ni - fastNotices.length];
				noticeKey = String(notice && (notice.key || notice.message) || "");
				if (noticeKey && seenNotices[noticeKey]) continue;
				if (noticeKey) seenNotices[noticeKey] = true;
				mergedNotices.push(notice);
			}
			initial = fullInitial;
			initial.notices = mergedNotices;
			initial.forceDialog = true;
		}
		var responseSeconds = Math.round((((new Date()).getTime() - startupStartedAt) / 1000) * 100) / 100;
		if (startupProgress) {
			startupProgress.complete(); startupProgress.close(); startupProgress = null;
		}
		if (showInterface) {
			var res = mainDialog(selection, initial, responseSeconds);
			if (!res || res.cancelled) {
				if (res && res.saveSettings) action.saveAcceptedSettings();
				else if (!actionPlaybackMode) cfg.save();
				$.setenv(APP.dialogEnvKey, "true");
				isCancelled = true;
				return;
			}
			action.saveAcceptedSettings();
			var generationStatus = generation.run(selection, res.schema, res.values);
			if (!generationStatus.keepDialog) $.setenv(APP.dialogEnvKey, "false");
			return;
		}
		if (!initial.schema) return;
		var silentProfile = backend.schemaProfile(initial.schema),
			silentValues = backend.profileValues(initial.schema, silentProfile);
		if (!actionPlaybackMode) cfg.saveToAction();
		try {
			var silentGenerationStatus = generation.run(selection, initial.schema, silentValues);
			if (!silentGenerationStatus.keepDialog) $.setenv(APP.dialogEnvKey, "false");
		} catch (silentGenerationError) {
			if (isUserCancellation(silentGenerationError))
				throw silentGenerationError;
			// GenerationRuntime may temporarily change layers and channels. Restore
			// the document before presenting controls for a corrected retry.
			restoreInitialDocumentState();
			$.setenv(APP.dialogEnvKey, "true");
			var generationNotice = {
				key: "silent-generation-error:" + errorMessageText(silentGenerationError),
				level: "error",
				message: errorMessageText(silentGenerationError)
			};
			if (initial.fastPath) {
				try {
					var expandedInitial = backend.loadInitialData(null, false);
					expandedInitial.notices = (expandedInitial.notices instanceof Array ? expandedInitial.notices : []).concat([generationNotice]);
					initial = expandedInitial;
				} catch (_) {
					initial.notices = (initial.notices instanceof Array ? initial.notices : []).concat([generationNotice]);
				}
			} else {
				initial.notices = (initial.notices instanceof Array ? initial.notices : []).concat([generationNotice]);
			}
			initial.forceDialog = true;
			var retryResult = mainDialog(selection, initial, responseSeconds);
			if (!retryResult || retryResult.cancelled) {
				if (retryResult && retryResult.saveSettings) action.saveAcceptedSettings();
				else if (!actionPlaybackMode) cfg.save();
				isCancelled = true;
				return;
			}
			action.saveAcceptedSettings();
			var retryGenerationStatus = generation.run(selection, retryResult.schema, retryResult.values);
			if (!retryGenerationStatus.keepDialog) $.setenv(APP.dialogEnvKey, "false");
		}
	} finally {
		if (startupProgress) { try { startupProgress.close(); } catch (_) { } startupProgress = null; }
	}
}
// Глобальные callback для Photoshop progress/suspendHistory.
function runWorkflowAnalysisProgress() { return backend.runWorkflowAnalysisProgress(); }
function workflowAnalysisStage() { return backend.workflowAnalysisStage(); }
function errorMessageText(value) {
	if (value === undefined || value === null) return "";
	if (value.message !== undefined) return String(value.message);
	return String(value);
}
// Photoshop сообщает нажатие Cancel несколькими способами: возвращает false
// из doProgress(), передаёт ответ backend с type=cancelled или выбрасывает
// системную ошибку 8007. Во всех случаях это один штатный исход без UI ошибки.
function userCancellationError() {
	var error = new Error(APP.cancelToken);
	error.img2imgUserCancelled = true;
	return error;
}
function isUserCancellation(value) {
	if (value === false) return true;
	if (!value) return false;
	if (value.img2imgUserCancelled === true) return true;
	if (String(value.type || "").toLowerCase() == "cancelled") return true;
	var number = Number(value.number);
	if (number == 8007 || number == -128) return true;
	var message = errorMessageText(value).replace(/^\s+|\s+$/g, "");
	if (message == APP.cancelToken) return true;
	return /^(?:error:\s*)?(?:user (?:cancelled|canceled)(?: the operation)?|operation (?:cancelled|canceled))[.!]?$/i.test(message);
}
function workflowDiagnosticText(item) {
	item = item && typeof item == "object" ? item : {};
	var sourceMessage = String(item.message || ""),
		code = String(item.code || ""), template = code && str[code] !== undefined
		? String(str[code])
		: sourceMessage,
		params = item.params instanceof Array ? item.params : [];
	for (var i = 0; i < params.length; i++)
		template = template.split("%" + (i + 1)).join(String(params[i]));
	return template;
}
function workflowDiagnosticIsInformational(diagnostic) {
	diagnostic = diagnostic && typeof diagnostic == "object" ? diagnostic : {};
	var level = String(diagnostic.level || "").toLowerCase(),
		code = String(diagnostic.code || "");
	// Ошибка никогда не скрывается, даже если использован информационный код.
	return level != "error" && (level == "info" || code == "size_not_found" || code == "sampler_not_found");
}
function schemaHasInformationalDiagnostics(schema) {
	var diagnostics = schema && schema.diagnostics instanceof Array ? schema.diagnostics : [];
	for (var i = 0; i < diagnostics.length; i++)
		if (workflowDiagnosticIsInformational(diagnostics[i])) return true;
	return false;
}
function apiErrorText(item) {
	item = item && typeof item == "object" ? item : {};
	var code = String(item.code || "");
	if (!code || str[code] === undefined) return String(item.message || "");
	var template = String(str[code]),
		params = item.params instanceof Array ? item.params : [];
	for (var i = 0; i < params.length; i++)
		template = template.split("%" + (i + 1)).join(String(params[i]));
	if (code == "workflow_not_ready" && item.details instanceof Array) {
		var details = [];
		for (i = 0; i < item.details.length; i++) {
			var detail = workflowDiagnosticText(item.details[i]);
			if (detail) details.push(detail);
		}
		if (details.length) template += "\n\n• " + details.join("\n• ");
	}
	return template;
}
function itemData(source) {
	var objectItem = source && typeof source == "object",
		label = objectItem ? (source.label !== undefined ? source.label : source.value) : source,
		value = objectItem && source.value !== undefined ? source.value : label,
		help = objectItem && source.help !== undefined ? source.help : "";
	return {
		label: String(label === undefined ? "" : label),
		value: value === undefined ? "" : value,
		help: String(help === undefined || help === null ? "" : help)
	};
}
function defaultComfyImageRole(candidate) {
	var meta = candidate && candidate.meta ? candidate.meta : {},
		source = String(meta.source_value === undefined || meta.source_value === null ? "" : meta.source_value)
			.replace(/^\s+|\s+$/g, "");
	if (meta.load_image && !source) return "empty";
	return "workflow";
}
// Только обычный CFG Scale управляет доступностью Negative prompt.
function findForgeCfgControlId(schema) {
	var controls = schema && schema.controls instanceof Array ? schema.controls : [];
	for (var i = 0; i < controls.length; i++) {
		var id = String(controls[i].id || ""),
			input = String(controls[i].input || "").toLowerCase(),
			payloadKey = String(controls[i].payload_key || "").toLowerCase();
		// Guidance и Distilled CFG не заменяют обычный CFG Scale.
		if (startsWithSemantic(id, "cfg")) return id;
		if (input == "cfg" || input == "cfg_scale" || payloadKey == "cfg_scale") return id;
	}
	return "";
}
function isCfgValueOne(value) {
	var number = parseFloat(value);
	return !isNaN(number) && number <= 1.000000001;
}
function forgeSchemaId(schema) {
	return schema ? schema.workspace_id || String(schema.workflow_id || "").replace(/^forge:/, "") : "";
}
// ГЛАВНОЕ ОКНО: state хранит данные динамической области.
function mainDialog(selection, initial, responseSeconds) {
	var selectionBounds = selection.bounds,
		state = {
			backend: initial.backend || cfg.activeBackend,
			workflows: initial.workflows || [],
			forgePresets: initial.forgePresets || [],
			forgeCatalog: initial.forgeCatalog || {},
			schema: initial.schema || null,
			notices: initial.notices instanceof Array ? initial.notices : [],
			noticeKeysShown: {},
			workflowDiagnosticSignature: "",
			emptyDropdownIds: initial.emptyDropdownIds instanceof Array ? initial.emptyDropdownIds : [],
			controls: {}, forgeLoraControl: null, comfyMainLabels: {}, result: null
		},
		mainControlLayout = [
			{ prefix: "checkpoint" },
			{ id: "modules" },
			{ prefix: "vae" },
			{ prefix: "text_encoder" },
			{ id: "positive_prompt" },
			{ special: "forge_loras" },
			{ prefix: "lora" },
			{ id: "negative_prompt" },
			{ id: "sampler" },
			{ id: "scheduler" },
			{ id: "steps" },
			{ id: "cfg" },
			{ id: "distilled_cfg_scale" },
			{ id: "shift" },
			{ id: "guidance" },
			{ id: "denoise" },
			{ id: "seed" }
		],
		w = new Window("dialog{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:15}"),
		gGlobal = w.add("group{orientation:'row',alignChildren:['left','center'],spacing:0,margins:[0,0,0," + MAIN_UI_SELECTION_HEADER_BOTTOM_MARGIN + "]}"),
		tWH = gGlobal.add("statictext"),
		gGlobalButtons = gGlobal.add("group{orientation:'row',alignChildren:['right','center'],spacing:0,margins:0}"),
		bLoadMetadata = gGlobalButtons.add("button"),
		bSettings = gGlobalButtons.add("button"),
		gSettingsHost = w.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:0}"),
		gSettings = null,
		gOk = w.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,10,0,0]}"),
		bOk = gOk.add("button", undefined, undefined, { name: "ok" });
	w.text = APP.name + " v" + VER + " — " + responseSeconds + "s";
	ui.setFixedWidth(gGlobal, ui.contentWidth());
	tWH.alignment = ["fill", "center"];
	tWH.minimumSize.width = 0;
	gGlobalButtons.alignment = ["right", "center"];
	ui.setFixedWidth(bLoadMetadata, ui.loadMetadataButtonWidth);
	ui.setFixedWidth(bSettings, ui.mainSettingsButtonWidth);
	updateSelectionSummary();
	bLoadMetadata.text = "LOAD"; bLoadMetadata.helpTip = str.loadLayerMetadata;
	bSettings.text = "⚙"; bSettings.helpTip = str.scriptSettings; bSettings.alignment = ["right", "center"];
	bOk.text = str.generate;
	updateMetadataButton(); showControls();
	var showInitialDiagnostics = !!state.schema;
	w.onShow = function () {
		activateVisibleDenoiseControl();
		var diagnosticSchema = showInitialDiagnostics ? state.schema : null;
		showInitialDiagnostics = false;
		showPendingNotices(diagnosticSchema);
	};
	bLoadMetadata.onClick = function () {
		var metadata = layerMetadata.read();
		if (!metadata) { updateMetadataButton(); return; }
		try {
			ui.runWithPaletteProgress(str.progressInitializing, function (progress) {
				if (!loadLayerGenerationSettings(metadata, progress)) throw new Error(str.errLayerMetadata);
			});
			updateMetadataButton();
		} catch (e) { messages.error(e); }
	};
	bSettings.onClick = function () {
		saveCurrentValues();
		var oldData = cloneObj(cfg.data), oldStatus = backend.getStatus(),
			settingsResult = showGlobalSettings(), probePerformed = settingsResult.probePerformed;
		if (probePerformed) state.forgeCatalog = {};
		if (!settingsResult.accepted) { backend.applyStatus(oldStatus); return; }
		if (probePerformed || oldData.backendHost != cfg.backendHost || oldData.forgePort != cfg.forgePort || oldData.forgeSchemasFolder != cfg.forgeSchemasFolder)
			state.forgeCatalog = {};
		try {
			ui.runWithPaletteProgress(str.progressInitializing, function (progress) {
				backend.applyStatus(api.handshake(
					progress, null, "cached", "", settingsResult.probeToken
				));
				backend.normalizeActiveBackend();
				if (!backend.hasAvailable()) throw new Error(str.errNoBackendAvailable);
				loadBackend(cfg.activeBackend, progress, true);
			});
			updateMetadataButton();
		} catch (e) {
			cfg.data = oldData; cfg.bindProperties();
			try { api.handshake(null, cfg, "silent"); } catch (_) { }
			backend.applyStatus(oldStatus);
			backend.normalizeActiveBackend();
			messages.error(e);
		}
	};
	bOk.onClick = function () {
		try {
			saveCurrentValues();
			if (!state.schema) return;
			if (!state.schema.valid) throw new Error(str.errWorkflowInvalid);
			state.result = { cancelled: false, backend: state.backend, schema: state.schema, values: collectValues() };
			w.close(1);
		} catch (e) { messages.error(e); }
	};
	w.onClose = function () {
		if (!state.result) {
			saveCurrentValues(); $.setenv(APP.dialogEnvKey, "true");
			state.result = { cancelled: true, saveSettings: true };
		}
		return true;
	};
	w.layout.layout(true); ui.setFixedWidth(w, ui.mainWindowWidth);
	ui.enableHoverFocus(w);
	w.center(); w.show(); return state.result;
	function updateSelectionSummary() {
		tWH.text = str.selection + selectionBounds.width + "x" + selectionBounds.height + " (" + roundTo(selectionBounds.width * selectionBounds.height / 1000000, 2) + " MP)";
	}
	function updateMetadataButton() {
		var hasMetadata = layerMetadata.read() != null,
			loadWidth = hasMetadata ? ui.loadMetadataButtonWidth : 0,
			buttonsWidth = ui.mainSettingsButtonWidth + loadWidth,
			textWidth = ui.headerTextWidth(hasMetadata);
		bLoadMetadata.visible = bLoadMetadata.enabled = hasMetadata;
		ui.setFixedWidth(bLoadMetadata, loadWidth);
		ui.setFixedWidth(bSettings, ui.mainSettingsButtonWidth);
		ui.setFixedWidth(gGlobalButtons, buttonsWidth);
		ui.setFixedWidth(tWH, textWidth);
		try { gGlobal.layout.layout(true); } catch (_) { }
	}
	function noticeKey(notice) {
		if (notice && notice.key !== undefined) return String(notice.key);
		return errorMessageText(notice);
	}
	function appendNotices(items) {
		items = items instanceof Array ? items : [];
		for (var i = 0; i < items.length; i++) {
			var msg = errorMessageText(items[i]), key = noticeKey(items[i]), duplicate = false;
			if (!msg || state.noticeKeysShown[key]) continue;
			for (var j = 0; j < state.notices.length; j++)
				if (noticeKey(state.notices[j]) == key) { duplicate = true; break; }
			if (!duplicate) state.notices.push(items[i]);
		}
	}
	function showPendingNotices(schema) {
		var warnings = [], errors = [], diagnostics = schema && schema.diagnostics instanceof Array ? schema.diagnostics : [],
			diagnosticTitle = str.workflowDiagnostics,
			informationProfile = schema && state.backend == BACKEND_COMFY ? backend.schemaProfile(schema) : null,
			showInformation = !!(informationProfile && informationProfile.workflowInformationSeen !== true),
			informationShown = false;
		for (var i = 0; i < state.notices.length; i++) {
			var msg = errorMessageText(state.notices[i]), key = noticeKey(state.notices[i]);
			if (!msg || state.noticeKeysShown[key]) continue;
			state.noticeKeysShown[key] = true;
			if (key.indexOf("silent-generation-error:") == 0) diagnosticTitle = str.generationDiagnostics;
			if (state.notices[i] && String(state.notices[i].level || "").toLowerCase() == "error") errors.push(msg);
			else warnings.push(msg);
		}
		state.notices = [];
		for (i = 0; i < diagnostics.length; i++) {
			var diagnosticText = workflowDiagnosticText(diagnostics[i]),
				level = String(diagnostics[i].level || "").toLowerCase(),
				informational = workflowDiagnosticIsInformational(diagnostics[i]);
			if (!diagnosticText) continue;
			if (informational) {
				if (showInformation) {
					warnings.push(diagnosticText);
					informationShown = true;
				}
				continue;
			}
			if (level == "error") errors.push(diagnosticText);
			else if (level == "warning") warnings.push(diagnosticText);
		}
		var schemaIdentity = schema
			? String(schema.workflow_id || schema.workspace_id || schema.relative_path || "")
			: "",
			signature = schemaIdentity + "\n--errors--\n" + errors.join("\n") + "\n--warnings--\n" + warnings.join("\n");
		if (!errors.length && !warnings.length) {
			state.workflowDiagnosticSignature = "";
			return;
		}
		if (signature == state.workflowDiagnosticSignature) return;
		state.workflowDiagnosticSignature = signature;
		messages.show({ title: diagnosticTitle, errors: errors, warnings: warnings });
		// Обычный повторный анализ и сохранение настроек сохраняют этот флаг;
		// полный сброс удаляет профиль и снова разрешает показ информации.
		if (informationShown) informationProfile.workflowInformationSeen = true;
	}
	// LOAD из XMP сначала переключает backend/workflow, затем переносит
	// сохранённые значения в соответствующий профиль и заново строит UI.
	function loadLayerGenerationSettings(metadata, progress) {
		if (!isObjectMap(metadata)) return false;
		var backendId = metadata.backend == BACKEND_FORGE ? BACKEND_FORGE : BACKEND_COMFY;
		if (!backend.isAvailable(backendId)) return false;
		loadBackend(backendId, progress, true);
		if (backendId == BACKEND_FORGE) {
			var presetId = metadata.workspace_id,
				presetItem = backend.findForgeSchema(state.forgePresets, presetId);
			if (!presetItem) return false;
			presetId = presetItem.id;
			cfg.selectedForgePreset = cfg.data.selectedForgePreset = presetId;
			var loadedForge = backend.loadForgeSchema(presetId, state.forgeCatalog, progress, false);
			state.forgeCatalog = loadedForge.catalog;
			state.schema = loadedForge.schema;
			var forgeProfile = cfg.getForgeProfile(presetId);
			applyMetadataToProfile(metadata, forgeProfile, ["autoResize", "resizePreset", "manualScale", "sizeMultiple", "selectedLoras", "imageStitchInputs", "selectedPromptPresets"]);
			showControls(); return true;
		}
		var workflow = null;
		if (metadata.workflow_id) workflow = backend.findWorkflow(state.workflows, metadata.workflow_id);
		if (!workflow && metadata.relative_path) for (var i = 0; i < state.workflows.length; i++) if (state.workflows[i].relative_path == metadata.relative_path) { workflow = state.workflows[i]; break; }
		if (!workflow) return false;
		cfg.selectedWorkflow = cfg.data.selectedWorkflow = workflow.id;
		var profile = cfg.getProfile(workflow.id), previous = cloneObj(profile.bindingOverrides);
		applyMetadataToProfile(metadata, profile, ["autoResize", "resizePreset", "manualScale", "sizeMultiple", "bindingOverrides", "referenceFiles", "outputFormat", "selectedPromptPresets"]);
		if (!isObjectMap(profile.bindingOverrides)) profile.bindingOverrides = { input: "", mask: "", references: [], referencesConfigured: false, emptyInputs: [], output: "", sizeMode: "auto", size: "" };
		if (profile.bindingOverrides.mask === undefined) profile.bindingOverrides.mask = "";
		if (!(profile.bindingOverrides.references instanceof Array)) profile.bindingOverrides.references = [];
		profile.bindingOverrides.referencesConfigured = profile.bindingOverrides.referencesConfigured === true;
		if (!(profile.bindingOverrides.emptyInputs instanceof Array)) profile.bindingOverrides.emptyInputs = [];
		if (profile.bindingOverrides.sizeMode !== "source_image" && profile.bindingOverrides.sizeMode !== "binding")
			profile.bindingOverrides.sizeMode = "auto";
		if (profile.bindingOverrides.sizeMode != "binding") profile.bindingOverrides.size = "";
		if (!bindingOverridesEqual(previous, profile.bindingOverrides)) {
			profile.schemaCache = null;
			profile.schemaCacheStamp = null;
			profile.schemaCacheVersion = 0;
			profile.schemaCacheUsed = 0;
		}
		reloadSelectedWorkflow(false, progress); return true;
	}
	// Значения слоя заменяют profile.values целиком. Это важно: скрытые
	// параметры от ранее выбранного профиля не должны примешиваться к LOAD.
	// Поля profile копируются только из явного whitelist allowed.
	function applyMetadataToProfile(metadata, profile, allowed) {
		if (isObjectMap(metadata.values)) profile.values = cloneObj(metadata.values);
		if (!isObjectMap(profile.values)) profile.values = {};
		if (isObjectMap(metadata.profile)) {
			for (var i = 0; i < allowed.length; i++)
				if (metadata.profile[allowed[i]] !== undefined)
					profile[allowed[i]] = cloneObj(metadata.profile[allowed[i]]);
		}
		if (allowed && arrayContains(allowed, "selectedLoras") && metadata.profile && metadata.profile.selectedLoras !== undefined)
			profile.lorasInitialized = true;
	}
	function loadBackend(backendId, progress, refresh) {
		if (!backend.isAvailable(backendId)) throw new Error(str.errBackendUnavailable);
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
	// ScriptUI-контролы не переиспользуются между схемами: старый контейнер
	// удаляется целиком. Это предотвращает устаревшие handlers и ошибки layout.
	function showControls() {
		appendNotices(backend.takeNotices());
		if (gSettings) { try { gSettings.visible = false; } catch (_) { } try { gSettingsHost.remove(gSettings); } catch (_) { } }
		gSettings = gSettingsHost.add("group{orientation:'column',alignChildren:['fill','top'],spacing:" + MAIN_UI_BLOCK_SPACING + ",margins:0}");
		ui.setFixedWidth(gSettings, ui.contentWidth());
		state.controls = {};
		state.forgeLoraControl = null;
		addBackendControl(gSettings);
		addSchemaControl(gSettings);
		var mainState = resolveMainState();
		if (mainState) {
			state.emptyDropdownIds = [];
			renderMainState(mainState);
			if (w.visible) showPendingNotices(state.schema);
			return;
		}
		var profile = backend.schemaProfile(state.schema),
			selectionValidation = backend.validateSchemaSelections(state.schema, profile);
		appendNotices(selectionValidation.notices);
		state.emptyDropdownIds = selectionValidation.emptyDropdownIds;
		fitSelectionBounds(selection, resolveProfileSizeMultiple(state.schema, profile)); updateSelectionSummary();
		var visible = state.backend == BACKEND_FORGE
			? resolveForgeVisibleControls(state.schema, profile)
			: profile.visibleControls;
		if (visible === null || visible === undefined) visible = state.schema.recommended_controls || [];
		var definitions = state.schema.controls || [], map = {}, i;
		for (i = 0; i < definitions.length; i++) map[definitions[i].id] = definitions[i];
		state.comfyMainLabels = state.backend == BACKEND_COMFY
			? buildComfyMainLabels(definitions, visible)
			: {};
		addDeclaredControls(gSettings, definitions, map, visible, profile);
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
		var baseGenerationEnabled = !!state.schema.valid && !!selection.result && !state.emptyDropdownIds.length;
		bOk.enabled = baseGenerationEnabled;
		if (state.backend == BACKEND_FORGE)
			applyForgeSchemaRules(state.schema, state.controls, baseGenerationEnabled);
		finalizeMainLayout();
		if (w.visible) showPendingNotices(state.schema);
	}
	function resolveMainState() {
		if (state.backend == BACKEND_COMFY && !backend.comfyFolderReady())
			return { text: str.infoMissingWorkflowFolder, height: 55, disablePanel: true, disableMetadata: true, keepSettingsEnabled: true };
		if (state.backend == BACKEND_FORGE && !backend.forgeFolderReady())
			return { text: str.infoMissingForgeSchemaFolder, height: 55, disablePanel: true, disableMetadata: true, keepSettingsEnabled: true };
		if (!state.schema)
			return { text: state.backend == BACKEND_FORGE ? str.infoEmptyForgePresets : str.infoEmptyWorkflowFolder + "\n" + cfg.workflowsFolder };
		return null;
	}
	function renderMainState(mainState) {
		fitSelectionBounds(selection, 1);
		updateSelectionSummary();
		var notice = gSettings.add("statictext", undefined, mainState.text, { multiline: true });
		if (mainState.height) notice.preferredSize = [ui.contentWidth(), mainState.height];
		if (mainState.disablePanel) gSettings.enabled = false;
		if (mainState.disableMetadata) bLoadMetadata.enabled = false;
		if (mainState.keepSettingsEnabled) bSettings.enabled = true;
		bOk.enabled = false;
		finalizeMainLayout();
	}
	function addDeclaredControls(parent, definitions, map, visible, profile) {
		for (var i = 0; i < mainControlLayout.length; i++) {
			var item = mainControlLayout[i];
			if (item.special == "forge_loras") addForgeLoraControl(parent, map, visible, profile);
			else if (item.prefix) addControlsByPrefix(item.prefix, parent, definitions, visible, profile);
			else addControlById(item.id, parent, map, visible, profile);
		}
	}
	function addForgeLoraControl(parent, map, visible, profile) {
		if (state.backend != BACKEND_FORGE || !map.positive_prompt || !arrayContains(visible, "positive_prompt")) return;
		state.forgeLoraControl = ui.addForgeLoraMultiSelect(
			parent,
			map.positive_prompt.forgeLoras || [],
			profile.selectedLoras || [],
			ui.contentWidth()
		);
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
	function addBackendControl(parent) {
		var items = [];
		if (backend.isAvailable(BACKEND_COMFY)) items.push({ label: "ComfyUI", value: BACKEND_COMFY });
		if (backend.isAvailable(BACKEND_FORGE)) items.push({ label: "Forge Neo", value: BACKEND_FORGE });
		if (items.length < 2) return;
		var control = ui.addDropdown(parent, str.backendLabel, items, ui.contentWidth(),
			[0, MAIN_UI_BACKEND_TOP_MARGIN, 0, MAIN_UI_BACKEND_BOTTOM_MARGIN]);
		ui.selectDropdown(control.dropdown, state.backend, 0);
		control.dropdown.onChange = function () {
			if (!this.selection) return;
			var selectedBackend = this.selection.controlValue || this.selection.text;
			if (selectedBackend == state.backend) return;
			saveCurrentValues();
			try { ui.runWithPaletteProgress(str.progressInitializing, function (progress) { loadBackend(selectedBackend, progress, true); }); }
			catch (e) { messages.error(e); showControls(); }
		};
	}
	function addSchemaControl(parent) {
		var description = schemaControlDescription(), group = ui.addColumn(parent,
			[0, MAIN_UI_SELECTOR_TOP_MARGIN, 0, MAIN_UI_SELECTOR_BOTTOM_MARGIN]);
		ui.setFixedWidth(group, ui.contentWidth());
		var title = group.add("statictext"), toolbar = ui.addToolbarRow(group, ui.contentWidth(), 4),
			dropdown = toolbar.dropdown, buttons = toolbar.controls, sel = 0;
		title.text = description.title;
		for (var buttonIndex = 0; buttonIndex < buttons.length; buttonIndex++) {
			buttons[buttonIndex].text = description.buttons[buttonIndex].text;
			buttons[buttonIndex].helpTip = description.buttons[buttonIndex].helpTip;
		}
		for (var i = 0; i < description.items.length; i++) {
			var source = description.items[i], item = dropdown.add("item", description.itemLabel(source));
			item.selectorValue = description.itemValue(source);
			if (String(item.selectorValue) == String(description.selectedValue)) sel = i;
		}
		if (dropdown.items.length) dropdown.selection = sel;
		var hasItems = description.items.length > 0;
		dropdown.enabled = hasItems;
		// Обновление должно оставаться доступным в существующей папке даже при
		// пустом списке: пользователь мог добавить JSON, пока окно было открыто.
		buttons[0].enabled = true;
		buttons[1].enabled = buttons[3].enabled = hasItems;
		buttons[2].enabled = hasItems && description.canSave;
		dropdown.onChange = function () { if (this.selection) schemaControlAction("select", this.selection.selectorValue); };
		buttons[0].onClick = function () { schemaControlAction("refresh"); };
		buttons[1].onClick = function () { schemaControlAction("rebuild"); };
		buttons[2].onClick = function () { schemaControlAction("save"); };
		buttons[3].onClick = function () { schemaControlAction("settings"); };
	}
	function schemaControlDescription() {
		var forge = state.backend == BACKEND_FORGE;
		return {
			title: forge ? str.uiPreset : str.workflow,
			items: forge ? state.forgePresets : state.workflows,
			selectedValue: forge ? cfg.selectedForgePreset : cfg.selectedWorkflow,
			canSave: schemaHasSavableVisibleValues(),
			itemLabel: forge
				? function (preset) {
					var label = String(preset.label || preset.id), duplicate = false;
					for (var j = 0; j < state.forgePresets.length; j++) {
						var other = state.forgePresets[j];
						if (other !== preset && String(other.label || other.id) == label) { duplicate = true; break; }
					}
					return duplicate ? label + " [" + String(preset.file || preset.id) + "]" : label;
				}
				: function (workflow) { return workflow.relative_path || workflow.name; },
			itemValue: function (item) { return item.id; },
			buttons: forge ? [
				{ text: "↻", helpTip: str.refreshForgeCatalog },
				{ text: "⟳", helpTip: str.rebuildForgeSchema },
				{ text: str.presetSaveButton, helpTip: str.saveForgeSchemaJson },
				{ text: "⚙", helpTip: str.forgeSchemaSettings }
			] : [
				{ text: "↻", helpTip: str.refreshWorkflows },
				{ text: "⟳", helpTip: str.rebuildWorkflow },
				{ text: str.presetSaveButton, helpTip: str.saveWorkflowJson },
				{ text: "⚙", helpTip: str.workflowSettings }
			]
		};
	}
	function schemaHasSavableVisibleValues() {
		if (!state.schema) return false;
		var profile = backend.schemaProfile(state.schema),
			forge = backend.schemaBackend(state.schema) == BACKEND_FORGE,
			visible = forge ? resolveForgeVisibleControls(state.schema, profile) : profile.visibleControls;
		if (visible === null || visible === undefined) visible = state.schema.recommended_controls || [];
		var controls = state.schema.controls instanceof Array ? state.schema.controls : [];
		for (var i = 0; i < controls.length; i++)
			if (controls[i] && controls[i].id && arrayContains(visible, controls[i].id)) return true;
		return forge && state.schema.capabilities && state.schema.capabilities.image_stitch &&
			arrayContains(visible, "image_stitch");
	}
	// ---
	// ДЕЙСТВИЯ TOOLBAR WORKFLOW / FORGE PRESET
	// Общий диспетчер вызывает backend-специфичный адаптер operations. Так
	// select/refresh/rebuild/save/settings имеют единый порядок сохранения.
	// ---
	function schemaControlAction(actionName, value) {
		var operations = schemaControlOperations();
		if (!operations) return;
		if (actionName == "select") {
			saveCurrentValues();
			runSchemaAction(function () { operations.select(value); });
			return;
		}
		if (actionName == "refresh") {
			runSchemaAction(function () {
				saveCurrentValues();
				operations.refresh();
			});
			return;
		}
		if (actionName == "rebuild") {
			if (!state.schema) return;
			var profileId = operations.profileId(),
				fullReset = messages.confirm(operations.rebuildConfirm, APP.name);
			if (fullReset === null) return;
			runSchemaAction(function () {
				if (fullReset) operations.resetProfile(profileId);
				else saveCurrentValues();
				operations.rebuild(profileId);
			});
			return;
		}
		if (actionName == "save") {
			if (!state.schema) return;
			var saveTarget;
			try { saveTarget = chooseSchemaSaveAsTarget(operations); }
			catch (e) { messages.error(e); return; }
			if (!saveTarget) return;
			runSchemaAction(function () {
				saveCurrentValues();
				operations.save(operations.profileId(), collectDisplayedControlValues(), saveTarget);
			});
			return;
		}
		if (actionName != "settings" || !state.schema) return;
		saveCurrentValues();
		var applySettings = operations.prepareSettings();
		if (!applySettings) return;
		runSchemaAction(applySettings, showControls);
	}
	function chooseSchemaSaveAsTarget(operations) {
		var relativePath = String(operations.sourceRelativePath() || "").replace(/\\/g, "/"),
			root = String(operations.rootFolder() || ""),
			sourceFile = new File(new Folder(root).fsName + "/" + relativePath),
			previousFolder = null, target = null;
		try {
			previousFolder = Folder.current;
			if (sourceFile.parent && sourceFile.parent.exists) Folder.current = sourceFile.parent;
			target = File.saveDialog(operations.savePrompt, "JSON:*.json");
		} finally {
			try { if (previousFolder) Folder.current = previousFolder; } catch (_) { }
		}
		if (!target) return null;
		if (!/\.json$/i.test(target.name)) throw new Error(str.errSaveAsJsonExtension);
		return target;
	}
	function normalizedFsPath(value) {
		try {
			var path = value instanceof File ? value.fsName : new File(String(value || "")).fsName;
			path = String(path || "").replace(/\\/g, "/");
			return String($.os || "").toLowerCase().indexOf("windows") >= 0 ? path.toLowerCase() : path;
		} catch (_) { return String(value || "").replace(/\\/g, "/"); }
	}
	function sameFsPath(left, right) {
		return !!left && !!right && normalizedFsPath(left) == normalizedFsPath(right);
	}
	function findBySavedPath(items, savedPath, rootPath, pathKey) {
		var root = new Folder(rootPath || "");
		for (var i = 0; i < items.length; i++) {
			var relativePath = String(items[i][pathKey] || "").replace(/\\/g, "/"),
				candidate = new File(root.fsName + "/" + relativePath);
			if (sameFsPath(candidate, savedPath)) return items[i];
		}
		return null;
	}

	function runSchemaAction(callback, after) {
		try { callback(); }
		catch (e) { messages.error(e); }
		finally { if (after) after(); }
	}
	function schemaControlOperations() {
		return state.backend == BACKEND_FORGE ? forgeSchemaOperations() : workflowOperations();
	}
	function workflowOperations() {
		return {
			rebuildConfirm: str.rebuildWorkflowConfirm,
			savePrompt: str.saveWorkflowAsPrompt,
			sourceRelativePath: function () { return state.schema ? state.schema.relative_path || "" : ""; },
			rootFolder: function () { return cfg.workflowsFolder || ""; },
			profileId: function () {
				return state.schema ? state.schema.workflow_id || cfg.selectedWorkflow : cfg.selectedWorkflow;
			},
			resetProfile: function (workflowId) { cfg.resetProfile(workflowId); },
			select: function (workflowId) {
				cfg.selectedWorkflow = cfg.data.selectedWorkflow = workflowId;
				ui.runWithPaletteProgress(str.progressInitializing, function (progress) {
					reloadSelectedWorkflow(false, progress);
				});
			},
			refresh: function () {
				state.workflows = ui.runWithPaletteProgress(str.progressWorkflows, function (progress) {
					return backend.refreshWorkflows(progress);
				});
				cfg.selectedWorkflow = cfg.data.selectedWorkflow = backend.chooseWorkflow(state.workflows);
				if (state.workflows.length) {
					ui.runWithPaletteProgress(str.progressInitializing, function (progress) {
						reloadSelectedWorkflow(false, progress);
					});
				} else {
					state.schema = null;
					showControls();
				}
			},
			rebuild: function () {
				ui.runWithPaletteProgress(str.progressAnalyze, function (progress) {
					reloadSelectedWorkflow(true, progress);
				});
			},
			save: function (workflowId, values, target) {
				var profile = cfg.getProfile(workflowId);
				ui.runWithPaletteProgress(str.progressSaveJson, function (progress) {
					var saved = api.workflowSaveValues(
						workflowId,
						state.schema.relative_path || profile.relativePath || "",
						profile.bindingOverrides,
						values,
						target.fsName,
						progress
					);
					state.workflows = backend.refreshWorkflows(progress);
					var savedWorkflow = findBySavedPath(state.workflows, saved.path, cfg.workflowsFolder, "relative_path");
					if (savedWorkflow) {
						cfg.selectedWorkflow = cfg.data.selectedWorkflow = savedWorkflow.id;
						reloadSelectedWorkflow(true, progress, true);
					}
				});
				showControls();
			},
			prepareSettings: function () {
				var profile = cfg.getProfile(state.schema.workflow_id),
					previous = cloneObj(profile.bindingOverrides);
				if (!showWorkflowSettings(state.schema, profile)) return null;
				var bindingsChanged = !bindingOverridesEqual(previous, profile.bindingOverrides);
				return function () {
					if (bindingsChanged) {
						profile.schemaCache = null;
						profile.schemaCacheStamp = null;
						profile.schemaCacheVersion = 0;
						profile.schemaCacheUsed = 0;
					}
					// Сохраняем принятые настройки до повторного анализа.
					// Если API снова вернёт ошибку сопоставления, выбранные
					// input/mask/output/size bindings всё равно останутся в
					// DESC или в параметрах текущего Photoshop Action.
					action.saveAcceptedSettings();
					if (bindingsChanged) {
						ui.runWithPaletteProgress(str.progressAnalyze, function (progress) {
							reloadSelectedWorkflow(false, progress, true);
						});
						// После успешного анализа сохраняем и обновлённый cache.
						action.saveAcceptedSettings();
					}
				};
			}
		};
	}
	function forgeSchemaOperations() {
		return {
			rebuildConfirm: str.rebuildForgeSchemaConfirm,
			savePrompt: str.saveForgeSchemaAsPrompt,
			sourceRelativePath: function () { return state.schema ? state.schema.relative_path || "" : ""; },
			rootFolder: function () { return cfg.forgeSchemasFolder || backend.defaultForgeFolder(); },
			profileId: function () { return forgeSchemaId(state.schema); },
			resetProfile: function (schemaId) { cfg.resetForgeProfile(schemaId); },
			select: function (schemaId) {
				cfg.selectedForgePreset = cfg.data.selectedForgePreset = schemaId;
				ui.runWithPaletteProgress(str.progressInitializing, function (progress) {
					loadForgeSchemaState(schemaId, progress, false);
				});
				showControls();
			},
			refresh: function () {
				ui.runWithPaletteProgress(str.progressForgeCatalog, function (progress) {
					// Пересканируем папку, а не только данные текущей схемы. Это позволяет
					// подхватить первый JSON из уже открытого окна с пустым списком.
					state.forgePresets = backend.refreshForgeSchemas(progress);
					cfg.selectedForgePreset = cfg.data.selectedForgePreset =
						backend.chooseForgeSchema(state.forgePresets);
					if (cfg.selectedForgePreset) loadForgeSchemaState(cfg.selectedForgePreset, progress, true);
					else state.schema = null;
				});
				showControls();
			},
			rebuild: function (schemaId) {
				ui.runWithPaletteProgress(str.progressForgePresets, function (progress) {
					state.forgePresets = backend.refreshForgeSchemas(progress);
					var presetItem = backend.findForgeSchema(state.forgePresets, schemaId);
					cfg.selectedForgePreset = cfg.data.selectedForgePreset = presetItem
						? presetItem.id
						: backend.chooseForgeSchema(state.forgePresets);
					if (cfg.selectedForgePreset) loadForgeSchemaState(cfg.selectedForgePreset, progress, true);
					else state.schema = null;
				});
				showControls();
			},
			save: function (schemaId, values, target) {
				var profile = cfg.getForgeProfile(schemaId);
				ui.runWithPaletteProgress(str.progressSaveJson, function (progress) {
					var saved = api.forgeSchemaSaveValues(schemaId, values, target.fsName, profile.selectedLoras || [], progress);
					state.forgePresets = backend.refreshForgeSchemas(progress);
					var savedSchema = findBySavedPath(state.forgePresets, saved.path, cfg.forgeSchemasFolder || backend.defaultForgeFolder(), "file");
					if (savedSchema) {
						cfg.selectedForgePreset = cfg.data.selectedForgePreset = savedSchema.id;
						loadForgeSchemaState(savedSchema.id, progress, false);
					}
				});
				showControls();
			},
			prepareSettings: function () {
				var profile = cfg.getForgeProfile(forgeSchemaId(state.schema));
				if (!showForgeSchemaSettings(state.schema, profile)) return null;
				return function () {
					ui.runWithPaletteProgress(str.progressForgeCatalog, function (progress) {
						state.forgeCatalog = backend.ensureForgeCatalog(state.schema, state.forgeCatalog, progress, false);
						state.schema = backend.hydrateForgeSchema(state.schema, state.forgeCatalog);
					});
				};
			}
		};
	}
	function loadForgeSchemaState(schemaId, progress, refresh) {
		var loaded = backend.loadForgeSchema(schemaId, state.forgeCatalog, progress, refresh);
		state.forgeCatalog = loaded.catalog;
		state.schema = loaded.schema;
	}
	function reloadSelectedWorkflow(force, progress, noShow) {
		var workflow = backend.findWorkflow(state.workflows, cfg.selectedWorkflow); if (!workflow) throw new Error(str.errSelectedWorkflowMissing);
		var profile = cfg.getProfile(workflow.id); profile.relativePath = workflow.relative_path || profile.relativePath || "";
		var schema = force ? null : cfg.getCachedSchema(workflow.id, workflow);
		if (!schema) { schema = backend.analyzeWorkflow(workflow, profile, force, progress); cfg.cacheSchema(schema, workflow); }
		state.schema = schema; if (!noShow) showControls();
	}
	// Главное окно Comfy показывает короткие подписи, но не меняет schema/bindings.
	// Сначала используем семантическое имя или имя input, затем добавляем
	// минимальный контекст ноды только при неоднозначности/слишком общем input.
	function buildComfyMainLabels(definitions, visible) {
		var entries = [], baseCounts = {}, finalCounts = {}, res = {}, i;
		for (i = 0; i < definitions.length; i++) {
			var definition = definitions[i], id = String(definition.id || "");
			if (!id || !arrayContains(visible, id)) continue;
			var info = ui.compactLabelInfo(definition), key = normalizeCompactLabel(info.base);
			entries.push({ definition: definition, info: info, baseKey: key, text: info.base });
			baseCounts[key] = (baseCounts[key] || 0) + 1;
		}
		for (i = 0; i < entries.length; i++) {
			var entry = entries[i], info = entry.info, needsContext = baseCounts[entry.baseKey] > 1 || info.lowInfo;
			if (info.valueOnly && info.valueTitle) entry.text = info.valueTitle;
			else if (info.valueOnly && info.context) entry.text = info.context;
			else if (needsContext && info.context) {
				entry.text = info.lowInfo && !info.semantic
					? info.context + " · " + info.base
					: info.base + " · " + info.context;
			}
			var finalKey = normalizeCompactLabel(entry.text);
			finalCounts[finalKey] = (finalCounts[finalKey] || 0) + 1;
		}
		for (i = 0; i < entries.length; i++) {
			var current = entries[i], definition = current.definition,
				text = current.text, finalKey = normalizeCompactLabel(text);
			if (finalCounts[finalKey] > 1) {
				var discriminator = String(definition.node_id || "");
				if (!discriminator) discriminator = String(definition.input || definition.id || "");
				if (discriminator) text += " · #" + discriminator;
			}
			res[String(definition.id || "")] = {
				label: text,
				help: ui.compactMainHelp(definition, text)
			};
		}
		return res;
	}
	function normalizeCompactLabel(value) {
		return String(value || "").toLowerCase().replace(/[^a-z0-9а-яё]+/g, "");
	}
	function addControlById(id, parent, map, visible, profile) { if (map[id] && arrayContains(visible, id)) addControlDefinition(parent, map[id], profile, ui.contentWidth()); }
	function addControlsByPrefix(prefix, parent, definitions, visible, profile) { for (var i = 0; i < definitions.length; i++) if (startsWithSemantic(definitions[i].id, prefix) && arrayContains(visible, definitions[i].id)) addControlDefinition(parent, definitions[i], profile, ui.contentWidth()); }
	function isPriorityMainControl(id) {
		for (var i = 0; i < mainControlLayout.length; i++) {
			var item = mainControlLayout[i];
			if (item.prefix ? startsWithSemantic(id, item.prefix) : id == item.id) return true;
		}
		return false;
	}
	function addControlDefinition(parent, definition, profile, preferredWidth) {
		var hasStoredValue = profile.values.hasOwnProperty(definition.id),
			stored = hasStoredValue ? profile.values[definition.id] : cloneObj(definition.value),
			displayDefinition = definition,
			displayInfo = state.comfyMainLabels[String(definition.id || "")];
		if (displayInfo) {
			displayDefinition = cloneObj(definition);
			displayDefinition.display_label = displayInfo.label;
			displayDefinition.display_help = displayInfo.help;
		}
		if (definition.type == "multiselect") {
			stored = ui.normalizeMultiselect(definition, stored);
			if (hasStoredValue) profile.values[definition.id] = cloneObj(stored);
		}
		state.controls[definition.id] = ui.addDynamic(parent, displayDefinition, stored, preferredWidth, {
			backend: backend.schemaBackend(state.schema),
			profile: profile
		});
	}

	// Сохраняет только реально созданные (видимые) контролы. Скрытые поля
	// остаются в профиле и не затираются при обычном переключении интерфейса.
	function saveCurrentValues() {
		if (!state.schema) return;
		var profile = backend.schemaProfile(state.schema),
			values = collectDisplayedControlValues();
		if (backend.schemaBackend(state.schema) == BACKEND_FORGE && state.forgeLoraControl)
			profile.selectedLoras = cloneObj(state.forgeLoraControl.getValue());
		for (var key in values) if (values.hasOwnProperty(key))
			profile.values[key] = cloneObj(values[key]);
	}
	function collectDisplayedControlValues() {
		var res = {}, key;
		for (key in state.controls) if (state.controls.hasOwnProperty(key) && state.controls[key] && state.controls[key].getValue)
			res[key] = state.controls[key].getValue();
		return res;
	}
	// Формирует полный набор для генерации. Для Forge скрытые контролы
	// дополняются проверенными default из схемы, поскольку endpoint ожидает
	// некоторые поля даже тогда, когда пользователь их не показывает.
	function collectValues() {
		var res = {}, key;
		for (key in state.controls) if (state.controls.hasOwnProperty(key)) res[key] = state.controls[key].getValue();
		if (state.schema && backend.schemaBackend(state.schema) == BACKEND_FORGE) {
			var definitions = state.schema.controls || [];
			for (var i = 0; i < definitions.length; i++) {
				var definition = definitions[i];
				if (!res.hasOwnProperty(definition.id)) res[definition.id] = cloneObj(definition.value);
			}
			if (state.schema.capabilities && state.schema.capabilities.image_stitch && !res.hasOwnProperty("image_stitch"))
				res.image_stitch = !!state.schema.image_stitch_default;
		}
		return res;
	}
	// Динамически включает зависимые контролы, Negative prompt и Generate.
	// Обработчики оборачиваются, а refreshing защищает от рекурсивного вызова
	// при onChanging/onChange одного и того же ScriptUI-контрола.
	function applyForgeSchemaRules(schema, controls, baseEnabled) {
		var generationRules = schema && schema.generation ? schema.generation : {},
			definitions = schema && schema.controls instanceof Array ? schema.controls : [],
			definitionMap = {}, watched = {}, refreshing = false, i, negativePromptId = "negative_prompt", cfgControlId;
		for (i = 0; i < definitions.length; i++) definitionMap[String(definitions[i].id || "")] = definitions[i];
		cfgControlId = findForgeCfgControlId(schema);
		function currentValue(id) {
			if (controls[id] && controls[id].getValue) return controls[id].getValue();
			return definitionMap[id] ? cloneObj(definitionMap[id].value) : false;
		}
		function truthy(value) {
			return toBooleanValue(value);
		}
		function assignHandler(control, name, handler) {
			if (!control) return;
			var previous = control[name];
			control[name] = function () {
				if (typeof previous == "function") previous.apply(this, arguments);
				return handler.apply(this, arguments);
			};
		}
		function refreshRules() {
			if (refreshing) return;
			refreshing = true;
			try {
				for (var j = 0; j < definitions.length; j++) {
					var definition = definitions[j], dependency = String(definition.enabled_by || ""), target;
					if (!dependency) continue;
					target = controls[String(definition.id || "")];
					if (target && target.container) target.container.enabled = truthy(currentValue(dependency));
				}
				if (controls[negativePromptId] && controls[negativePromptId].container) {
					var negativeEnabled = true;
					if (cfgControlId) negativeEnabled = !isCfgValueOne(currentValue(cfgControlId));
					controls[negativePromptId].container.enabled = negativeEnabled;
				}
				var allowed = true, required = generationRules.require_any;
				if (required instanceof Array && required.length) {
					allowed = false;
					for (var k = 0; k < required.length; k++)
						if (truthy(currentValue(String(required[k])))) { allowed = true; break; }
				}
				bOk.enabled = baseEnabled && allowed;
			} finally {
				refreshing = false;
			}
		}
		function watch(id) {
			id = String(id || "");
			if (!id || watched[id] || !controls[id] || !controls[id].control) return;
			watched[id] = true;
			assignHandler(controls[id].control, "onClick", refreshRules);
			assignHandler(controls[id].control, "onChange", refreshRules);
			assignHandler(controls[id].control, "onChanging", refreshRules);
		}
		for (i = 0; i < definitions.length; i++) watch(definitions[i].enabled_by);
		if (generationRules.require_any instanceof Array)
			for (i = 0; i < generationRules.require_any.length; i++) watch(generationRules.require_any[i]);
		watch(cfgControlId);
		refreshRules();
	}
	// ---
	// РЕДАКТОР СОСТАВА ИНТЕРФЕЙСА
	// Общая оболочка используется и Comfy workflow, и Forge schema; Comfy
	// дополнительно вставляет редактор input/mask/output/size bindings.
	// ---
	function showSchemaSettings(options) {
		var w = ui.createDialog({ title: options.title, spacing: 8, margins: 15 }),
			accepted = false;
		ui.addMultilineNote(w, options.note, 540, options.noteHeight);
		var context = options.addSpecificControls ? options.addSpecificControls(w) : null,
			visibleEditor = ui.addVisibleControlsEditor(w, options.visibleEditor);
		ui.addAcceptRow(w, str.saveChanges, function () {
			if (options.apply(visibleEditor, context) === false) return;
			accepted = true;
			w.close(1);
		});
		ui.showDialog(w);
		return accepted;
	}
	function showWorkflowSettings(schema, profile) {
		var visible = profile.visibleControls;
		if (visible === null || visible === undefined) visible = schema.recommended_controls || [];
		return showSchemaSettings({
			title: str.workflowSettings,
			note: str.workflowTagNote,
			noteHeight: 52,
			addSpecificControls: function (parent) {
				return addWorkflowBindingEditor(parent, schema, profile);
			},
			visibleEditor: {
				title: str.visibleParameters,
				height: 250,
				controls: schema.controls || [],
				visible: visible,
				recommendedIds: schema.recommended_controls || [],
				recommendedText: str.recommended,
				allText: str.all,
				noneText: str.none,
				sizeLabel: str.sizeMultiple,
				sizeValue: profile.sizeMultiple || cfg.sizeMultiple,
				outputFormatLabel: str.outputImageFormat,
				outputFormatValue: profile.outputFormat,
				outputFormatItems: [
					{ label: "JPG", value: "jpg" },
					{ label: "PNG", value: "png" }
				],
				// Для generic Comfy controls пользователю полезнее видеть
				// понятное имя ноды и стабильный internal control id, который
				// используется в binding. Хвост вида "[#170:168]: value" здесь
				// избыточен и заменяется на "[node_170:168__value]".
				itemLabel: function (definition) {
					var label = String(ui.label(definition) || "");
					label = label.replace(/\s*\[#.+?\]\s*:\s*[^\]]+\s*$/, "");
					return label + " [" + definition.id + "]";
				}
			},
			apply: function (editor, bindings) {
				if (!bindings.apply()) return false;
				profile.visibleControls = editor.selectedIds();
				profile.sizeMultiple = clamp(parseInt(editor.multiple.text, 10) || cfg.sizeMultiple, 1, 256);
				profile.outputFormat = normalizeOutputFormat(editor.outputFormat && editor.outputFormat.selection
					? editor.outputFormat.selection.controlValue : "jpg");
				return true;
			}
		});
	}
	function showForgeSchemaSettings(schema, profile) {
		var schemaSizeMultiple = clamp(parseInt(schema.size_multiple, 10) || cfg.sizeMultiple, 1, 256);
		return showSchemaSettings({
			title: str.forgeSchemaSettings,
			note: str.forgeSchemaSettingsNote,
			noteHeight: 44,
			visibleEditor: {
				title: str.visibleParameters,
				height: 270,
				controls: forgeEditorControls(schema),
				visible: resolveForgeVisibleControls(schema, profile),
				recommendedIds: schema.recommended_controls || [],
				recommendedText: str.recommended,
				allText: str.all,
				noneText: str.none,
				sizeLabel: str.sizeMultiple,
				sizeValue: resolveProfileSizeMultiple(schema, profile),
				isRequired: isRequiredForgeControl,
				itemLabel: function (definition, required) {
					var suffix = required ? str.alwaysVisible : "";
					return forgeSettingsGroup(definition).label + " · " + ui.label(definition) +
						"  [" + definition.id + "]" + (suffix ? " — " + suffix : "");
				},
				itemHelp: function (definition) {
					var value = definition.value;
					if (value instanceof Array) value = value.join(", ");
					else if (value && typeof value == "object") value = jsonStringify(value);
					return ui.help(definition) + "\n" + str.schemaDefaultValue + String(value === undefined ? "" : value);
				}
			},
			apply: function (editor) {
				profile.visibleControls = editor.selectedIds();
				var selectedSizeMultiple = clamp(parseInt(editor.multiple.text, 10) || schemaSizeMultiple, 1, 256);
				profile.sizeMultiple = selectedSizeMultiple == schemaSizeMultiple ? null : selectedSizeMultiple;
				return true;
			}
		});
	}
	function addWorkflowBindingEditor(parent, schema, profile) {
		var candidates = schema.candidates || {},
			inputCandidates = candidates.input || [],
			roleCandidates = [],
			roleById = {},
			storedMainId = String(profile.bindingOverrides.input || ""),
			automaticInputTargets = schema && schema.bindings && schema.bindings.input_image instanceof Array
				? schema.bindings.input_image : [],
			automaticReferenceBindings = schema && schema.bindings && schema.bindings.reference_images instanceof Array
				? schema.bindings.reference_images : [],
			rolesPanel = parent.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
		rolesPanel.text = str.imageInputRoles;
		var roleList = rolesPanel.add("listbox"),
			roleEditRow = rolesPanel.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
			roleTitle = roleEditRow.add("statictext{preferredSize:[175,-1]}"),
			roleDropdown = roleEditRow.add("dropdownlist{preferredSize:[340,-1]}"),
			workflowFile = rolesPanel.add("statictext", undefined, "", { multiline: true }),
			roleEditorBusy = false;
		roleList.preferredSize = [520, 115];
		workflowFile.preferredSize = [520, 30];
		roleTitle.text = str.selectedInputRole;
		var maskRow = addCandidateDropdownRow(parent, str.inpaintMask),
			maskDropdown = maskRow.dropdown;
		maskRow.label.helpTip = maskDropdown.helpTip = str.maskUsageHelp;
		function candidateById(items, id) {
			for (var ci = 0; ci < items.length; ci++)
				if (String(items[ci].id || "") == String(id || "")) return items[ci];
			return null;
		}
		function candidatesOverlap(first, second) {
			var a = first && first.targets instanceof Array ? first.targets : [],
				b = second && second.targets instanceof Array ? second.targets : [];
			for (var ai = 0; ai < a.length; ai++) for (var bi = 0; bi < b.length; bi++)
				if (String(a[ai].node_id || "") == String(b[bi].node_id || "") &&
					String(a[ai].input || "") == String(b[bi].input || "")) return true;
			return false;
		}
		function targetKeys(candidate) {
			var res = {}, targets = candidate && candidate.targets instanceof Array ? candidate.targets : [];
			for (var i = 0; i < targets.length; i++)
				res[String(targets[i].node_id || "") + ":" + String(targets[i].input || "")] = true;
			return res;
		}
		function targetsEqual(candidate, targets) {
			var first = targetKeys(candidate), second = targetKeys({ targets: targets }), key,
				firstCount = 0, secondCount = 0;
			for (key in first) if (first.hasOwnProperty(key)) firstCount++;
			for (key in second) if (second.hasOwnProperty(key)) {
				secondCount++;
				if (!first[key]) return false;
			}
			return firstCount == secondCount && firstCount > 0;
		}
		function selectedMainCandidates() {
			var res = [];
			for (var i = 0; i < roleCandidates.length; i++)
				if (roleById[roleCandidates[i].id] == "photoshop") res.push(roleCandidates[i]);
			return res;
		}
		function groupedMainCandidate(selected) {
			if (!selected.length) return null;
			if (selected.length == 1) return selected[0];
			var selectedKeys = {}, selectedCount = 0, i, key;
			for (i = 0; i < selected.length; i++) {
				var keys = targetKeys(selected[i]);
				for (key in keys) if (keys.hasOwnProperty(key) && !selectedKeys[key]) {
					selectedKeys[key] = true; selectedCount++;
				}
			}
			for (i = 0; i < inputCandidates.length; i++) {
				var candidate = inputCandidates[i], meta = candidate.meta || {};
				if (!meta.grouped) continue;
				var groupKeys = targetKeys(candidate), groupCount = 0, matches = true;
				for (key in groupKeys) if (groupKeys.hasOwnProperty(key)) {
					groupCount++; if (!selectedKeys[key]) matches = false;
				}
				if (matches && groupCount == selectedCount) return candidate;
			}
			return null;
		}
		function currentMainCandidate() { return groupedMainCandidate(selectedMainCandidates()); }
		function displayCandidateName(candidate) {
			return String(candidate && candidate.label || candidate && candidate.id || "")
				.replace(/\s+→\s+[^→]+\s*$/, "");
		}
		function workflowSource(candidate) {
			var meta = candidate && candidate.meta ? candidate.meta : {};
			return String(meta.source_value === undefined || meta.source_value === null ? "" : meta.source_value);
		}
		function roleLabel(role) {
			if (role == "photoshop") return str.rolePhotoshop;
			if (role == "reference") return str.roleReference;
			if (role == "empty") return str.roleEmpty;
			return str.roleWorkflowFile;
		}
		function initializeRoles() {
			var main = storedMainId
					? candidateById(inputCandidates, storedMainId)
					: (automaticInputTargets.length ? { targets: automaticInputTargets } : null),
				referenceIds = profile.bindingOverrides.references || [],
				referencesConfigured = profile.bindingOverrides.referencesConfigured === true,
				emptyIds = profile.bindingOverrides.emptyInputs || [], i, j, candidate, selected;
			for (i = 0; i < inputCandidates.length; i++)
				if (!(inputCandidates[i].meta && inputCandidates[i].meta.grouped)) roleCandidates.push(inputCandidates[i]);
			for (i = 0; i < roleCandidates.length; i++) {
				candidate = roleCandidates[i];
				roleById[candidate.id] = defaultComfyImageRole(candidate);
				if (main && candidatesOverlap(main, candidate)) roleById[candidate.id] = "photoshop";
				for (j = 0; j < referenceIds.length; j++) {
					selected = candidateById(inputCandidates, referenceIds[j]);
					if (selected && candidatesOverlap(selected, candidate)) roleById[candidate.id] = "reference";
				}
				if (!referencesConfigured && !referenceIds.length && roleById[candidate.id] != "photoshop")
					for (j = 0; j < automaticReferenceBindings.length; j++)
						if (candidatesOverlap(candidate, { targets: automaticReferenceBindings[j].targets || [] })) {
							roleById[candidate.id] = "reference";
							break;
						}
				for (j = 0; j < emptyIds.length; j++) {
					selected = candidateById(inputCandidates, emptyIds[j]);
					if (selected && candidatesOverlap(selected, candidate)) roleById[candidate.id] = "empty";
				}
			}
		}
		function renderRoleList(selectedId) {
			roleList.removeAll();
			var selectedIndex = 0;
			for (var i = 0; i < roleCandidates.length; i++) {
				var candidate = roleCandidates[i], role = roleById[candidate.id],
					label = displayCandidateName(candidate) + "  —  " + roleLabel(role),
					item;
				item = roleList.add("item", label);
				item.candidateId = candidate.id;
				item.helpTip = "";
				if (candidate.id == selectedId) selectedIndex = i;
			}
			if (roleList.items.length) roleList.selection = selectedIndex;
			updateRoleEditor();
		}
		function updateRoleEditor() {
			var item = roleList.selection,
				candidate = item ? candidateById(roleCandidates, item.candidateId) : null,
				role = candidate ? roleById[candidate.id] : "workflow",
				roles = [
					{ value: "photoshop", label: str.rolePhotoshop },
					{ value: "reference", label: str.roleReference },
					{ value: "workflow", label: str.roleWorkflowFile }
				];
			if (candidate && candidate.meta && candidate.meta.load_image)
				roles.push({ value: "empty", label: str.roleEmpty });
			roleEditorBusy = true;
			roleDropdown.removeAll();
			var selectedIndex = 0;
			for (var i = 0; i < roles.length; i++) {
				var roleItem = roleDropdown.add("item", roles[i].label);
				roleItem.roleValue = roles[i].value;
				if (roles[i].value == role) selectedIndex = i;
			}
			if (roleDropdown.items.length) roleDropdown.selection = selectedIndex;
			roleDropdown.enabled = !!candidate;
			var source = workflowSource(candidate), showWorkflowFile = !!candidate && role == "workflow";
			workflowFile.text = showWorkflowFile ? str.workflowStoredFile + (source || str.fileNotSpecified) : "";
			workflowFile.helpTip = showWorkflowFile ? (source || str.fileNotSpecified) : "";
			workflowFile.visible = showWorkflowFile;
			try { rolesPanel.layout.layout(true); } catch (_) { }
			roleEditorBusy = false;
		}
		function currentMaskCandidates() {
			var main = currentMainCandidate(), inputId = main ? main.id : "",
				mainByInput = candidates.main_mask_by_input,
				allMasks = candidates.mask || [], res = [], dynamicMask = null;
			if (inputId && mainByInput && mainByInput[inputId])
				dynamicMask = mainByInput[inputId];
			if (dynamicMask) res.push(dynamicMask);
			for (var i = 0; i < allMasks.length; i++)
				if (!allMasks[i].meta || allMasks[i].meta.mode != "input_alpha") res.push(allMasks[i]);
			return res;
		}
		function rebuildMaskDropdown(preferredId) {
			var maskCandidates = currentMaskCandidates(),
				main = currentMainCandidate(),
				selectedId = preferredId === undefined ? candidateId(maskDropdown) : String(preferredId || ""),
				selectedIndex = 0,
				i, item;
			maskDropdown.removeAll();
			item = maskDropdown.add("item", str.automatic);
			item.candidateId = "";
			for (i = 0; i < maskCandidates.length; i++) {
				var maskMeta = maskCandidates[i].meta || {},
					maskLabel = maskMeta.mode == "input_alpha"
						? "MASK: " + displayCandidateName(main)
						: String(maskCandidates[i].label || maskCandidates[i].id || "");
				maskLabel += " — " + (maskMeta.connected ? str.maskUsedByWorkflow : str.maskUnusedByWorkflow);
				item = maskDropdown.add("item", maskLabel);
				item.candidateId = maskCandidates[i].id;
				item.maskConnected = !!maskMeta.connected;
				if (String(maskCandidates[i].id || "") == selectedId) selectedIndex = i + 1;
			}
			if (maskDropdown.items.length) maskDropdown.selection = selectedIndex;
			maskDropdown.enabled = maskDropdown.items.length > 1;
		}
		initializeRoles();
		renderRoleList(roleCandidates.length ? roleCandidates[0].id : "");
		roleList.onChange = updateRoleEditor;
		roleDropdown.onChange = function () {
			if (roleEditorBusy || !roleList.selection || !this.selection) return;
			var selectedCandidateId = roleList.selection.candidateId,
				nextRole = this.selection.roleValue;
			if (nextRole == "photoshop")
				for (var i = 0; i < roleCandidates.length; i++)
					if (roleCandidates[i].id != selectedCandidateId && roleById[roleCandidates[i].id] == "photoshop")
						roleById[roleCandidates[i].id] = "workflow";
			roleById[selectedCandidateId] = nextRole;
			renderRoleList(selectedCandidateId);
			rebuildMaskDropdown(candidateId(maskDropdown));
		};
		rebuildMaskDropdown(profile.bindingOverrides.mask);
		var outputDropdown = addCandidateDropdown(parent, str.outputImage, candidates.output || [], profile.bindingOverrides.output, true),
			sizeModeDropdown = addCandidateDropdown(parent, str.sizeControlMode, [
				{ id: "auto", label: str.sizeModeAuto },
				{ id: "source_image", label: str.sizeModeSourceImage },
				{ id: "binding", label: str.sizeModeBinding }
			], profile.bindingOverrides.sizeMode || "auto", false),
			sizeDropdown = addCandidateDropdown(parent, str.primarySize, candidates.size || [], profile.bindingOverrides.size, false),
			sizeStatus = ui.addMultilineNote(parent, "", 540, 34);
		function currentSizeMode() {
			return sizeModeDropdown && sizeModeDropdown.selection
				? String(sizeModeDropdown.selection.candidateId || "auto")
				: "auto";
		}
		function selectedSizeLabel() {
			return sizeDropdown && sizeDropdown.selection ? String(sizeDropdown.selection.text || "") : "";
		}
		function updateSizeModeUi() {
			var mode = currentSizeMode(),
				hasCandidates = !!(sizeDropdown && sizeDropdown.items && sizeDropdown.items.length);
			sizeDropdown.enabled = mode == "binding" && hasCandidates;
			if (mode == "source_image") {
				sizeStatus.text = str.sizeModeSourceHelp;
			} else if (mode == "binding") {
				sizeStatus.text = hasCandidates
					? str.sizeModeBindingHelp + (selectedSizeLabel() ? "\n" + selectedSizeLabel() : "")
					: str.sizeModeNoCandidates;
			} else {
				var schemaWasAutomatic = schema && String(schema.size_selection_mode || "auto") == "auto",
					automaticLabel = schemaWasAutomatic && schema.bindings && schema.bindings.size
						? String(schema.bindings.size.label || "")
						: "";
				sizeStatus.text = automaticLabel
					? str.sizeModeAutoSelected + " " + automaticLabel
					: (schemaWasAutomatic ? str.sizeModeAutoFallback : str.sizeModeAutoHelp);
			}
		}
		sizeModeDropdown.onChange = updateSizeModeUi;
		sizeDropdown.onChange = updateSizeModeUi;
		updateSizeModeUi();
		return {
			apply: function () {
				var nextSizeMode = currentSizeMode(),
					nextSizeId = nextSizeMode == "binding" ? candidateId(sizeDropdown) : "",
					main = currentMainCandidate(), errors = [], i, candidate;
				if (!selectedMainCandidates().length) errors.push(str.errMainImageRoleRequired);
				else if (!main) errors.push(str.errMainImageRoleConflict);
				if (nextSizeMode == "binding" && !nextSizeId) errors.push(str.errSizeBindingRequired);
				if (candidateId(maskDropdown) && maskDropdown.selection && !maskDropdown.selection.maskConnected)
					errors.push(str.errSelectedMaskDisconnected);
				for (i = 0; i < roleCandidates.length; i++) {
					candidate = roleCandidates[i];
					if (roleById[candidate.id] == "empty" && !(candidate.meta && candidate.meta.load_image))
						errors.push(displayCandidateName(candidate) + ": " + str.errEmptyRoleUnsupported);
				}
				if (errors.length)
					messages.show({ title: str.workflowDiagnostics, errors: errors });
				if (errors.length) return false;
				profile.bindingOverrides.input = main && (!storedMainId && targetsEqual(main, automaticInputTargets))
					? "" : (main ? main.id : "");
				profile.bindingOverrides.references = [];
				profile.bindingOverrides.referencesConfigured = true;
				profile.bindingOverrides.emptyInputs = [];
				for (i = 0; i < roleCandidates.length; i++) {
					candidate = roleCandidates[i];
					if (roleById[candidate.id] == "reference") profile.bindingOverrides.references.push(candidate.id);
					else if (roleById[candidate.id] == "empty") profile.bindingOverrides.emptyInputs.push(candidate.id);
				}
				profile.bindingOverrides.mask = candidateId(maskDropdown);
				profile.bindingOverrides.output = candidateId(outputDropdown);
				profile.bindingOverrides.sizeMode = nextSizeMode;
				profile.bindingOverrides.size = nextSizeId;
				return true;
			}
		};
	}
	function forgeEditorControls(schema) {
		var res = [], controls = schema && schema.controls instanceof Array ? schema.controls : [], i;
		for (i = 0; i < controls.length; i++) res.push(controls[i]);
		if (schema && schema.capabilities && schema.capabilities.image_stitch)
			res.push({ id: "image_stitch", label: "ImageStitch", value: !!schema.image_stitch_default });
		res.sort(function (first, second) {
			var a = forgeSettingsGroup(first), b = forgeSettingsGroup(second);
			if (a.order != b.order) return a.order - b.order;
			var al = String(first.label || first.id || "").toLowerCase(), bl = String(second.label || second.id || "").toLowerCase();
			return al == bl ? 0 : (al > bl ? 1 : -1);
		});
		return res;
	}
	function forgeSettingsGroup(definition) {
		var id = String(definition && definition.id || "").toLowerCase(), payload = String(definition && definition.payload_key || "").toLowerCase();
		if (startsWithSemantic(id, "checkpoint") || id == "modules" || startsWithSemantic(id, "vae") || startsWithSemantic(id, "text_encoder"))
			return { order: 0, label: str.forgeGroupModel };
		if (startsWithSemantic(id, "positive_prompt") || startsWithSemantic(id, "negative_prompt") || startsWithSemantic(id, "lora"))
			return { order: 1, label: str.forgeGroupPrompt };
		if (startsWithSemantic(id, "sampler") || startsWithSemantic(id, "scheduler") || startsWithSemantic(id, "steps") ||
			startsWithSemantic(id, "cfg") || startsWithSemantic(id, "guidance") || startsWithSemantic(id, "denoise") || startsWithSemantic(id, "seed"))
			return { order: 2, label: str.forgeGroupSampling };
		if (id == "image_stitch" || /image|mask|width|height|resize|upscal/.test(id + " " + payload))
			return { order: 3, label: str.forgeGroupImage };
		return { order: 4, label: str.forgeGroupAdvanced };
	}
	function isRequiredForgeControl(definition) {
		if (!definition) return false;
		if (definition.required_visible) return true;
		var id = String(definition.id || "");
		return startsWithSemantic(id, "checkpoint") || id == "modules" || startsWithSemantic(id, "vae") || startsWithSemantic(id, "text_encoder");
	}
	function addCandidateDropdown(parent, labelText, candidates, selectedId, includeAutomatic) {
		var dropdown = addCandidateDropdownRow(parent, labelText).dropdown;
		var sel = 0, offset = 0;
		if (includeAutomatic) {
			var automatic = dropdown.add("item", str.automatic);
			automatic.candidateId = "";
			offset = 1;
		}
		for (var i = 0; i < candidates.length; i++) {
			var item = dropdown.add("item", candidates[i].label);
			item.candidateId = candidates[i].id;
			if (candidates[i].id == selectedId) sel = i + offset;
		}
		if (dropdown.items.length) dropdown.selection = sel;
		return dropdown;
	}
	function addCandidateDropdownRow(parent, labelText) {
		var row = parent.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
			label = row.add("statictext{preferredSize:[175,-1]}"),
			dropdown = row.add("dropdownlist{preferredSize:[360,-1]}");
		label.text = labelText;
		return { row: row, label: label, dropdown: dropdown };
	}
	function candidateId(dropdown) {
		return dropdown && dropdown.selection ? dropdown.selection.candidateId : "";
	}
	// ГЛОБАЛЬНЫЕ НАСТРОЙКИ: cfg изменяется только после Save.
	function showGlobalSettings() {
		var temp = cloneObj(cfg.data),
			w = ui.createDialog({ title: str.scriptSettings, spacing: 10, margins: 14 }),
			connection = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
		connection.text = str.connectionSettings;
		var connectionRows = ui.addFormRows(connection, [
				{
					id: "status", type: "static", label: str.detectedBackends, value: backend.statusLabel(),
					labelWidth: 120, controlWidth: 220, justify: "center",
					button: { text: "↻", helpTip: str.detectBackends, width: 35, height: 25 }
				},
				{ id: "host", label: str.host, value: temp.backendHost || "127.0.0.1", labelWidth: 105, controlWidth: 275 },
				{ id: "comfyPort", label: str.comfyPort, value: String(temp.comfyPort || 8188), labelWidth: 105, controlWidth: 70 },
				{ id: "forgePort", label: str.forgePort, value: String(temp.forgePort || 7860), labelWidth: 105, controlWidth: 70 },
				{
					id: "workflowsFolder", label: str.workflowFolder, value: temp.workflowsFolder || "", readOnly: true,
					labelWidth: 105, controlWidth: 240, button: { text: "...", width: 25, height: 25 }
				},
				{
					id: "forgeSchemasFolder", label: str.forgeSchemaFolder, value: temp.forgeSchemasFolder || backend.defaultForgeFolder(), readOnly: true,
					labelWidth: 105, controlWidth: 240, button: { text: "...", width: 25, height: 25 }
				}
			], ui.settingsControlWidth),
			statusValue = connectionRows.status.control,
			testConnection = connectionRows.status.button,
			hostEdit = connectionRows.host.control,
			comfyPortRow = connectionRows.comfyPort.row,
			comfyPortEdit = connectionRows.comfyPort.control,
			forgePortRow = connectionRows.forgePort.row,
			forgePortEdit = connectionRows.forgePort.control,
			folderRow = connectionRows.workflowsFolder.row,
			folderEdit = connectionRows.workflowsFolder.control,
			browse = connectionRows.workflowsFolder.button,
			forgeFolderRow = connectionRows.forgeSchemasFolder.row,
			forgeFolderEdit = connectionRows.forgeSchemasFolder.control,
			forgeBrowse = connectionRows.forgeSchemasFolder.button;
		browse.onClick = function () { var folder = Folder.selectDialog(str.selectWorkflowFolder); if (folder) folderEdit.text = folder.fsName; };
		forgeBrowse.onClick = function () { var folder = Folder.selectDialog(str.selectForgeSchemaFolder); if (folder) forgeFolderEdit.text = folder.fsName; };
		var pythonIdleSeconds = parseInt(temp.pythonIdleTimeout, 10);
		if (isNaN(pythonIdleSeconds)) pythonIdleSeconds = 15 * 60;
		var pythonServer = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
		pythonServer.text = str.pythonServerSettings;
		var pythonRows = ui.addFormRows(pythonServer, [
				{
					id: "version", type: "static", label: str.pythonApiVersion,
					value: backend.pythonVersion() ? "v" + backend.pythonVersion() : "—",
					labelWidth: 220, controlWidth: 100
				},
				{
					id: "idleTimeout", label: str.pythonIdleTimeout,
					value: String(Math.round(pythonIdleSeconds / 60)),
					labelWidth: 220, controlWidth: 65
				},
				{
					id: "monitorInterval", label: str.backendMonitorInterval,
					value: String(temp.backendMonitorInterval || 5),
					labelWidth: 220, controlWidth: 65
				}
			], ui.settingsControlWidth),
			pythonIdleTimeout = pythonRows.idleTimeout.control,
			backendMonitorInterval = pythonRows.monitorInterval.control;
		function updateBackendFields() {
			var comfyMode = temp.activeBackend != BACKEND_FORGE;
			comfyPortRow.enabled = folderRow.enabled = comfyMode;
			forgePortRow.enabled = forgeFolderRow.enabled = !comfyMode;
		}
		updateBackendFields();
		var probePerformed = false, probeToken = "";
		testConnection.onClick = function () {
			var probe = cloneObj(temp);
			probe.backendHost = String(hostEdit.text || "").replace(/^\s+|\s+$/g, "") || "127.0.0.1";
			probe.comfyPort = clamp(parseInt(comfyPortEdit.text, 10) || 8188, 1, 65535);
			probe.forgePort = clamp(parseInt(forgePortEdit.text, 10) || 7860, 1, 65535);
			probe.workflowsFolder = folderEdit.text || "";
			probe.forgeSchemasFolder = forgeFolderEdit.text || "";
			try {
				probePerformed = true;
				probeToken = "";
				ui.runWithPaletteProgress(str.progressHandshake, function (progress) {
					var probeStatus = api.probeBackends(probe, progress);
					probeToken = String(probeStatus.probe_token || "");
					backend.applyStatus(probeStatus);
				});
				statusValue.text = backend.statusLabel();
			} catch (e) { messages.error(e); }
		};
		var resize = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
		resize.text = str.resizePresetManagement;
		var resizeEditor = resizePresetEditor(resize, temp);
		var output = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
		output.text = str.imageSettings;
		var outputFields = ui.addCheckboxes(output, [
				{ id: "flatten", text: str.flatten, value: temp.flatten },
				{ id: "rasterize", text: str.rasterize, value: temp.rasterizeImage },
				{ id: "keepAspectRatio", text: str.keepAspectRatioDuringPlace, value: temp.keepAspectRatioDuringPlace }
			]),
			flatten = outputFields.flatten,
			rasterize = outputFields.rasterize,
			keepAspectRatio = outputFields.keepAspectRatio;
		var brush = w.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
		brush.text = str.brushSettings;
		var brushFields = ui.addCheckboxes(brush, [{ id: "selectBrush", text: str.selectBrush, value: temp.selectBrush }]),
			selectBrush = brushFields.selectBrush,
			opacityControl = ui.addSlider(brush, str.opacity, 1, 100, temp.brushOpacity, { displayValue: temp.brushOpacity, controlWidth: ui.settingsControlWidth }),
			opacityStepper = createSliderStepper(opacityControl.slider, 1, 1);
		function syncOpacityValue(finalize) {
			var value = finalize ? opacityStepper.finish() : opacityStepper.sync(false);
			opacityControl.valueText.text = Math.round(value);
		}
		opacityControl.slider.onChange = function () { syncOpacityValue(true); };
		opacityControl.slider.onChanging = function () { syncOpacityValue(false); };
		var generalFields = ui.addCheckboxes(w, [
				{ id: "recordSettings", text: str.recordSettingsToAction, value: temp.recordSettingsToAction },
				{ id: "metadata", text: str.layerMetadata, value: temp.writeLayerMetadata }
			]),
			recordSettings = generalFields.recordSettings,
			metadata = generalFields.metadata,
			timeoutFields = ui.addFormRows(w, [{
				id: "timeout", label: str.generationTimeout, value: String(temp.generationTimeout), labelWidth: 220, controlWidth: 65
			}]),
			timeout = timeoutFields.timeout.control;
		function resizePresetEditor(parent, tempCfg) {
			if (!tempCfg.resizePresets || !tempCfg.resizePresets.length) tempCfg.resizePresets = cloneObj(presets.defaultResize());
			var toolbar = ui.addPresetToolbar(parent, ui.settingsControlWidth, str.presetRestore),
				presetList = toolbar.dropdown,
				// Slider Max MP хранит значение в сотых MP (2.0 MP = 200),
				// чтобы ScriptUI работал с целыми шагами без ошибок float.
				minControl = presetSlider(parent, {
					title: str.minimumSide, min: 256, max: 4096, value: 512, step: 32, suffix: " px"
				}),
				maxControl = presetSlider(parent, {
					title: str.maximumMp, min: 10, max: 1200, value: 200, step: 10, suffix: " MP"
				}),
				minSync = minControl.slider.onChange,
				maxSync = maxControl.slider.onChange;
			minControl.slider.onChange = function () { minSync.call(this); checkIntegrity(); };
			maxControl.slider.onChange = function () { maxSync.call(this); checkIntegrity(); };
			toolbar.refresh.onClick = function () { loadSelection(); };
			toolbar.add.onClick = function () {
				var cur = readPreset(),
					defaultName = presetList.selection ? tempCfg.resizePresets[presetList.selection.index].name + str.presetCopy : str.resizePresetNew,
					name = messages.prompt(str.resizePresetPrompt, defaultName, str.resizePresetTitle);
				name = name == null ? "" : String(name).replace(/^\s+|\s+$/g, "");
				if (!name.length) return;
				var found = presets.findResizeIndex(name, tempCfg.resizePresets);
				if (found >= 0) {
					if (messages.confirm(String(str.errResizePreset).replace("%1", name), str.resizePresetTitle) !== true) return;
					tempCfg.resizePresets[found] = presets.createResize(name, cur.minSide, cur.maxMp);
				} else {
					tempCfg.resizePresets.push(presets.createResize(name, cur.minSide, cur.maxMp));
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
				minControl.syncValue(true);
				maxControl.syncValue(true);
				checkIntegrity();
			}
			function checkIntegrity() {
				if (!presetList.selection) {
					toolbar.refresh.enabled = toolbar.save.enabled = toolbar.remove.enabled = false;
					return;
				}
				var cur = readPreset(),
					preset = tempCfg.resizePresets[presetList.selection.index],
					changed = cur.minSide != preset.minSide || cur.maxMp != preset.maxMp;
				toolbar.refresh.enabled = toolbar.save.enabled = changed;
				toolbar.remove.enabled = tempCfg.resizePresets.length > 1 && !presets.isProtectedResize(preset.name);
			}
			function readPreset() {
				return {
					minSide: Math.round(minControl.slider.value / 32) * 32,
					maxMp: Math.round(maxControl.slider.value / 10) * 10 / 100
				};
			}
			function saveActive(refresh) {
				if (!presetList.selection) return false;
				var cur = readPreset(), index = presetList.selection.index, preset = tempCfg.resizePresets[index];
				if (cur.minSide == preset.minSide && cur.maxMp == preset.maxMp) return false;
				tempCfg.resizePresets[index] = presets.createResize(preset.name, cur.minSide, cur.maxMp);
				if (refresh) refreshList(index); else checkIntegrity();
				return true;
			}
			return { saveActive: function () { return saveActive(false); } };
		}
		function presetSlider(parent, options) {
			var group = parent.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:0}"),
				titleGroup = group.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}");
			ui.setFixedWidth(group, ui.settingsControlWidth);
			var label = titleGroup.add('statictext'),
				valueText = titleGroup.add('statictext{justify:"right"}'),
				slider = group.add('slider'),
				control = {
					slider: slider,
					value: valueText,
					suffix: options.suffix,
					decimal: options.suffix == ' MP',
					stepper: null
				};
			label.text = options.title;
			label.alignment = ['fill', 'center'];
			valueText.alignment = ['right', 'center'];
			slider.minvalue = options.min;
			slider.maxvalue = options.max;
			slider.value = options.value;
			control.stepper = createSliderStepper(slider, options.step, options.min);
			try { ui.enableHoverFocus(slider); } catch (_) { }
			function syncValue(reset, finalize) {
				var value = reset
					? control.stepper.reset()
					: (finalize ? control.stepper.finish() : control.stepper.sync(false));
				control.value.text = (control.decimal ? value / 100 : value) + control.suffix;
			}
			slider.onChanging = function () { syncValue(false, false); };
			slider.onChange = function () { syncValue(false, true); };
			control.syncValue = function (reset) { syncValue(!!reset, false); };
			syncValue(true, false);
			return control;
		}
		var accepted = false;
		ui.addAcceptRow(w, str.saveChanges, function () {
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
			var idleMinutes = parseInt(pythonIdleTimeout.text, 10);
			if (isNaN(idleMinutes)) idleMinutes = 15;
			temp.pythonIdleTimeout = clamp(idleMinutes, 0, 7 * 24 * 60) * 60;
			temp.backendMonitorInterval = clamp(parseInt(backendMonitorInterval.text, 10) || 5, 2, 300);
			if (folderChanged) { temp.workflowCatalog = []; temp.selectedWorkflow = ""; }
			if (forgeFolderChanged) { temp.forgeCatalog = []; temp.selectedForgePreset = ""; }
			cfg.data = temp; cfg.bindProperties(); accepted = true; w.close(1);
		});
		ui.showDialog(w);
		return { accepted: accepted, probePerformed: probePerformed, probeToken: probeToken };
	}
}
// REFERENCE / IMAGESTITCH
function isSupportedReferenceImage(path) {
	return /\.(?:jpe?g|png|webp)$/i.test(String(path || ""));
}
function imageStitchInputLimit(schema) {
	var capabilities = schema && schema.capabilities ? schema.capabilities : {},
		value = parseInt(capabilities.max_image_inputs, 10);
	if (isNaN(value)) value = 3;
	return clamp(value, 1, 3);
}
function arrayContainsCaseInsensitive(array, value) {
	value = String(value).toUpperCase();
	for (var i = 0; i < array.length; i++) if (String(array[i]).toUpperCase() == value) return true;
	return false;
}
function normalizeForgeLoraList(items) {
	var res = [];
	if (!(items instanceof Array)) return res;
	for (var i = 0; i < items.length; i++) {
		var item = items[i], name = "";
		if (typeof item == "string") name = item;
		else if (item && typeof item == "object")
			name = item.name || item.alias || item.value || item.label || item.model_name || item.title || item.filename || item.path || "";
		name = String(name || "").replace(/^\s+|\s+$/g, "");
		if (name && !arrayContainsCaseInsensitive(res, name)) res.push(name);
	}
	res.sort(function (a, b) {
		a = String(a).toLowerCase(); b = String(b).toLowerCase();
		return a == b ? 0 : (a > b ? 1 : -1);
	});
	return res;
}
function parseForgeLoraEntry(value) {
	var text = String(value === undefined || value === null ? "" : value).replace(/^\s+|\s+$/g, ""),
		prefixRemoved,
		name = "",
		weight = 1,
		separator = -1,
		parsedWeight;
	text = text.replace(/^<+|>+$/g, "");
	prefixRemoved = /^lora:/i.test(text) ? text.substring(5) : text;
	separator = prefixRemoved.lastIndexOf(":");
	if (separator > 0) {
		name = prefixRemoved.substring(0, separator).replace(/^\s+|\s+$/g, "");
		parsedWeight = parseFloat(prefixRemoved.substring(separator + 1));
		if (!isNaN(parsedWeight)) weight = parsedWeight;
		else name = prefixRemoved.replace(/^\s+|\s+$/g, "");
	} else {
		name = prefixRemoved.replace(/^\s+|\s+$/g, "");
	}
	weight = clamp(roundTo(weight, 2), 0, 1);
	return { name: name, weight: weight };
}
function formatForgeLoraWeight(value) {
	var weight = parseFloat(value);
	if (isNaN(weight)) weight = 1;
	weight = clamp(roundTo(weight, 2), 0, 1);
	return formatNumber(weight, false, 2);
}
function formatForgeLoraEntry(name, weight) {
	name = String(name || "").replace(/^\s+|\s+$/g, "");
	return name ? "lora:" + name + ":" + formatForgeLoraWeight(weight) : "";
}
function normalizeForgeLoraEntries(value) {
	var source = value instanceof Array ? value : [],
		res = [],
		names = [];
	for (var i = 0; i < source.length; i++) {
		var item = source[i], parsed = null, name = "", weight;
		if (typeof item == "string") parsed = parseForgeLoraEntry(item);
		else if (item && typeof item == "object") {
			name = item.name || item.lora || item.value || item.label || item.id || item.file || item.filename || "";
			weight = item.weight;
			if (weight === undefined) weight = item.scale;
			if (weight === undefined) weight = item.strength;
			parsed = parseForgeLoraEntry(formatForgeLoraEntry(name, weight));
		}
		if (!parsed || !parsed.name || arrayContainsCaseInsensitive(names, parsed.name)) continue;
		names.push(parsed.name);
		res.push(formatForgeLoraEntry(parsed.name, parsed.weight));
	}
	return res;
}
function resolveForgeLoraSelection(items, storedValue, requireAvailable) {
	var available = normalizeForgeLoraList(items),
		source = normalizeForgeLoraEntries(storedValue),
		selected = [],
		missing = [],
		selectedNames = [];
	for (var i = 0; i < source.length; i++) {
		var parsed = parseForgeLoraEntry(source[i]),
			name = parsed.name,
			matchedName = name;
		if (available.length || requireAvailable) {
			matchedName = "";
			for (var j = 0; j < available.length; j++)
				if (String(available[j]).toUpperCase() == String(name).toUpperCase()) {
					matchedName = available[j];
					break;
				}
			if (!matchedName) {
				if (requireAvailable && !arrayContainsCaseInsensitive(missing, name)) missing.push(name);
				continue;
			}
		}
		if (arrayContainsCaseInsensitive(selectedNames, matchedName)) continue;
		selectedNames.push(matchedName);
		selected.push(formatForgeLoraEntry(matchedName, parsed.weight));
	}
	return { selected: selected, missing: missing };
}
function normalizeForgeLoraSelection(items, storedValue) {
	return resolveForgeLoraSelection(items, storedValue, false).selected;
}
function hasDeclaredForgeLoras(schema) {
	return !!(schema && schema.loras instanceof Array && schema.loras.length);
}
// ---
// ЦИКЛ ГЕНЕРАЦИИ И РАБОТА С ДОКУМЕНТОМ PHOTOSHOP
// Отвечает за экспорт выделения, формирование запроса, progress, получение
// результата, размещение слоя, маску и метаданные.
// ---
function GenerationRuntime() {
	var placementResultFile = null,
		placementSelection = null;
	function isSeedControl(schema) {
		var id = String(schema.id || "").toLowerCase(),
			input = String(schema.input || "").toLowerCase();
		return id == "seed" || id.indexOf("seed__") == 0 || input == "seed" || input == "noise_seed";
	}
	function makeRandomUiSeed(schema) {
		var min = parseInt(schema.min, 10),
			max = parseInt(schema.max, 10);
		if (isNaN(min) || min < 0) min = 0;
		if (isNaN(max) || max > 4294967295 || max <= min) max = 4294967295;
		return min + Math.floor(Math.random() * (max - min + 1));
	}
	function collectReferenceFiles(schema, profile) {
		var res = [],
			bindings = schema && schema.bindings ? (schema.bindings.reference_images || []) : [];
		if (!profile.referenceFiles) return res;
		for (var i = 0; i < bindings.length; i++) {
			var path = profile.referenceFiles[bindings[i].id] || "";
			if (path) res.push({ binding_id: bindings[i].id, path: path });
		}
		return res;
	}
	// Возвращает только существующие уникальные JPG/JPEG/PNG/WebP и применяет
	// лимит схемы. Пустой результат допустим и просто отключает ImageStitch.
	function collectForgeImageInputs(schema, profile) {
		var res = [],
			values = profile.imageStitchInputs instanceof Array ? profile.imageStitchInputs : [],
			limit = imageStitchInputLimit(schema);
		for (var i = 0; i < values.length && res.length < limit; i++) {
			if (!values[i] || !isSupportedReferenceImage(values[i])) continue;
			var file = new File(values[i]);
			if (file.exists && !arrayContainsCaseInsensitive(res, file.fsName)) res.push(file.fsName);
		}
		return res;
	}
	function getProfileTargetSize(bounds, profile, schema) {
		var scale = toBooleanValue(profile.autoResize)
			? autoScale(bounds, presets.findResize(profile.resizePreset, cfg.resizePresets))
			: profile.manualScale;
		return calculateSizeFromScale(bounds.width, bounds.height, scale || 1, resolveProfileSizeMultiple(schema, profile));
	}
	function run(selection, schema, values) {
		var requestId = createRequestId(),
			inputFile = null,
			maskFile = null,
			resultFile = null,
			keepDialog = false;
		try {
			var currentBackend = backend.schemaBackend(schema),
				profile = backend.schemaProfile(schema),
				inpaintMode = getComfyInpaintMode(selection, schema);
			fitSelectionBounds(selection, resolveProfileSizeMultiple(schema, profile));
			var targetSize = getProfileTargetSize(selection.bounds, profile, schema),
				width = targetSize.width,
				height = targetSize.height;
			app.activeDocument.suspendHistory(localize(str.historyPrepareSelection), "prepareSelectionLayer(selection)");
			var directComfyFolder = currentBackend == BACKEND_COMFY ? backend.comfyUploadFolder() : "",
				exportedFiles;
			if (directComfyFolder) {
				try {
					exportedFiles = exportSelectionFiles(selection, width, height, requestId, inpaintMode, directComfyFolder);
				} catch (_) {
					// Путь мог устареть после перезапуска ComfyUI или оказаться
					// недоступным Photoshop. Временный файл сохранит HTTP-fallback.
					exportedFiles = exportSelectionFiles(selection, width, height, requestId, inpaintMode, "");
				}
			} else {
				exportedFiles = exportSelectionFiles(selection, width, height, requestId, inpaintMode, "");
			}
			inputFile = exportedFiles.input;
			maskFile = exportedFiles.mask;
			var msg;
			if (currentBackend == BACKEND_FORGE) {
				msg = {
					schema_id: schema.workspace_id || String(schema.workflow_id || "").replace(/^forge:/, ""),
					schema_folder: cfg.forgeSchemasFolder || "",
					input: inputFile.fsName,
					width: width,
					height: height,
					values: cloneObj(values),
					selected_loras: cloneObj(profile.selectedLoras || []),
					image_inputs: collectForgeImageInputs(schema, profile),
					timeout: cfg.generationTimeout
				};
			} else {
				msg = {
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
					output_format: normalizeOutputFormat(profile.outputFormat),
					timeout: cfg.generationTimeout
				};
			}
			var command = {
				protocol: API_PROTOCOL,
				request_id: requestId,
				type: currentBackend == BACKEND_FORGE ? "forge_generate" : "generate",
				message: msg
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
			var progressCompleted;
			try {
				progressCompleted = app.doProgress(progressTitles.window, "runGenerationProgress()");
			} catch (progressError) {
				if (!isUserCancellation(progressError)) throw progressError;
				progressCompleted = false;
			}
			var progressResult = generationProgress.getResult();
			if (progressCompleted === false || isUserCancellation(progressResult)) {
				throw userCancellationError();
			}
			if (!progressResult) throw new Error(str.errNoResult);
			if (progressResult.type == "error") throw new Error(apiErrorText(progressResult));
			var answer = progressResult.message,
				resultPath = typeof answer == "object" ? answer.path : answer;
			resultFile = new File(resultPath);
			if (!resultFile.exists) throw new Error(str.errResultFile + "\n" + resultPath);
			// Метаданные записывают именно отправленные значения и профиль
			// размещения. LOAD может затем воспроизвести состояние независимо
			// от текущих настроек главного окна.
			var metadataProfile = {
				autoResize: profile.autoResize,
				resizePreset: profile.resizePreset,
				manualScale: profile.manualScale,
				sizeMultiple: resolveProfileSizeMultiple(schema, profile),
				selectedLoras: currentBackend == BACKEND_FORGE ? cloneObj(profile.selectedLoras) : [],
				selectedPromptPresets: cloneObj(profile.selectedPromptPresets || { positive: "", negative: "" }),
				bindingOverrides: currentBackend == BACKEND_COMFY ? cloneObj(profile.bindingOverrides) : {},
				referenceFiles: currentBackend == BACKEND_COMFY ? cloneObj(profile.referenceFiles) : {},
				imageStitchInputs: currentBackend == BACKEND_FORGE ? cloneObj(profile.imageStitchInputs) : []
			};
			if (currentBackend == BACKEND_COMFY) metadataProfile.outputFormat = normalizeOutputFormat(profile.outputFormat);
			layerMetadata.set({
				backend: currentBackend,
				workspace_id: currentBackend == BACKEND_FORGE
					? (schema.workspace_id || String(schema.workflow_id || "").replace(/^forge:/, ""))
					: schema.workflow_id,
				workflow_id: currentBackend == BACKEND_COMFY ? schema.workflow_id : "",
				relative_path: schema.relative_path || profile.relativePath || "",
				workflow_hash: typeof answer == "object" ? answer.workflow_hash || "" : "",
				prompt_id: typeof answer == "object" ? answer.prompt_id || "" : "",
				values: values,
				generated_seeds: typeof answer == "object" ? answer.generated_seeds || {} : {},
				profile: metadataProfile,
				width: width,
				height: height
			});
			placementResultFile = resultFile;
			placementSelection = selection;
			try {
				app.activeDocument.suspendHistory(localize(str.historyPlaceResult), "placeResultHistory()");
				// С этого момента результат принадлежит документу. Ошибки
				// обновления seed, DESC или Action-параметров не должны
				// откатывать историю к initialState.
				generationResultPlaced = true;
			} finally {
				placementResultFile = null;
				placementSelection = null;
			}
			advanceVisibleSeeds(schema, profile, values);
			try {
				action.saveAfterGeneration();
			} catch (saveError) {
				// Сохранение настроек является постобработкой, а не частью
				// самой генерации. Результат уже размещён и остаётся в
				// документе; пользователю показывается отдельная ошибка.
				$.setenv(APP.dialogEnvKey, "true");
				keepDialog = true;
				messages.error(
					str.errSettingsSaveAfterGeneration +
					"\n" + errorMessageText(saveError) +
					(saveError && saveError.line ? "\n\n" + str.jsxLine + saveError.line : ""),
					APP.name
				);
			}
			if (typeof answer == "object" && answer.warnings instanceof Array && answer.warnings.length) {
				var localizedGenerationWarnings = [];
				for (var warningIndex = 0; warningIndex < answer.warnings.length; warningIndex++)
					localizedGenerationWarnings.push(workflowDiagnosticText(answer.warnings[warningIndex]));
				messages.show({ title: str.generationDiagnostics, warnings: localizedGenerationWarnings });
			}
			return { keepDialog: keepDialog };
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
		if (!binding || !binding.mode) throw new Error(str.errInpaintMaskMissing);
		if (!binding.connected) {
			if (binding.mode == "input_alpha") throw new Error(str.errInpaintInputDisconnected);
			throw new Error(str.errInpaintNodeDisconnected);
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
			window: backendLabel + ": " + str.generationProgressTitle,
			prepare: backendLabel + ": " + str.progressInitializeAction + " " + subject + "… ",
			generate: backendLabel + ": " + str.progressGenerateAction + "… "
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
		// checkSelection() обычно уже выводит документ из Quick Mask. Эта
		// проверка оставлена как защита от изменения режима между проверкой
		// выделения и началом генерации. Сам inpaint определяется только
		// сохранённым флагом selection.inpaint.
		if (doc.getProperty("quickMask")) doc.quickMask("clearEvent");
		if (doc.hasProperty("selection")) {
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
	}
	function exportSelectionFiles(selection, width, height, requestId, inpaintMode, preferredFolder) {
		var hst = activeDocument.activeHistoryState,
			hiddenLayerIds = [],
			c = null;
		try { c = doc.getProperty("center").value; } catch (_) { }
		var p = preferredFolder
			? new Folder(String(preferredFolder))
			: new Folder(Folder.temp.fsName + "/" + APP.tempFolder);
		if (!p.exists && !p.create()) throw new Error(str.errSaveJpeg);
		// Для обоих вариантов Comfy inpaint Photoshop экспортирует обычный
		// JPEG и отдельную маску. Это повторяет проверенную схему SD Helper и
		// не требует создавать вторую user mask после Merge Visible.
		var inputFile = new File(p.fsName + "/IMG2IMG_" + requestId + ".jpg"),
			maskFile = inpaintMode ? new File(p.fsName + "/INPAINT_MASK_" + requestId + ".png") : null;
		try {
			if (inpaintMode) {
				doc.selectLayersByIDs([selection.junk]);
				lr.selectChannel("mask");
				doc.copyPixels();
			}
			if (cfg.flatten) {
				// Composite нужен только для отправки. Он создаётся после сохранения
				// hst и полностью удаляется возвратом к этой записи истории в finally.
				// Служебный слой с маской скрывается, чтобы не попасть во входное
				// изображение; новый пустой слой гарантирует работу Merge Visible
				// даже тогда, когда в документе был только один видимый слой.
				doc.selectLayersByIDs([selection.junk]);
				doc.hideSelectedLayers();
				doc.makeLayer(APP.generatedLayerName);
				doc.mergeVisible();
			} else {
				hiddenLayerIds = hideLayersAboveSource(selection.junk);
			}
			doc.makeSelection(selection.bounds);
			doc.crop(true);
			doc.flatten();
			if (inpaintMode) doc.pastePixels();
			resizeDocument(width, height);
			if (maskFile) {
				doc.saveAPNGCopy(maskFile);
				doc.deleteLayer();
			}
			doc.saveACopy(inputFile);
		} catch (exportError) {
			try { if (inputFile.exists) inputFile.remove(); } catch (_) { }
			try { if (maskFile && maskFile.exists) maskFile.remove(); } catch (_) { }
			throw exportError;
		} finally {
			activeDocument.activeHistoryState = hst;
			// History State обычно возвращает видимость, но Photoshop не всегда
			// полностью восстанавливает индивидуальные visible-флаги вложенных
			// слоёв после Hide + Flatten. Поэтому явно показываем только те
			// верхнеуровневые слои/группы, которые скрипт сам скрыл и которые
			// до экспорта были видимы. Внутренняя видимость групп не меняется.
			if (hiddenLayerIds.length) {
				try {
					doc.selectLayersByIDs(hiddenLayerIds);
					doc.showSelectedLayers();
					doc.selectLayersByIDs([selection.junk]);
				} catch (_) { }
			}
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
				from = lr.getProperty("itemIndex", false, layerId) +
					(doc.getProperty("hasBackgroundLayer") ? 0 : 1),
				ids = [],
				groupDepth = 0;
			// Индексы перебираются снизу вверх. Для группы сначала встречается
			// layerSectionEnd, затем её содержимое и только потом заголовок
			// layerSectionStart. Пока groupDepth > 0, вложенные элементы не
			// добавляются: при закрытии диапазона выбирается сама группа.
			// layerSectionStart при depth == 0 является родительской группой
			// исходного слоя; её скрывать нельзя, поэтому она пропускается.
			for (var i = from; i <= length; i++) {
				var section = lr.getProperty("layerSection", false, i, true),
					sectionValue = section ? section.value : "";
				if (sectionValue == "layerSectionEnd") {
					groupDepth++;
					continue;
				}
				if (sectionValue == "layerSectionStart") {
					if (groupDepth > 0) {
						groupDepth--;
						if (groupDepth == 0) addVisibleLayer(i);
					}
					continue;
				}
				if (sectionValue == "layerSectionContent" && groupDepth == 0)
					addVisibleLayer(i);
			}
			if (from <= length && ids.length) {
				doc.selectLayersByIDs(ids);
				doc.hideSelectedLayers();
			}
			return ids;
			function addVisibleLayer(index) {
				var id = lr.getProperty("layerID", false, index, true),
					visible = true;
				try { visible = !!lr.getProperty("visible", false, id); }
				catch (_) { }
				if (visible && !arrayContains(ids, id)) ids.push(id);
			}
		}
	}
	// Place создаёт Smart Object. Затем изображение растягивается либо
	// пропорционально вписывается в исходное выделение и получает маску.
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
		// Загрузка выделения из маски временного слоя в некоторых версиях
		// Photoshop может сделать этот временный слой активным. Перед созданием
		// итоговой маски явно возвращаемся к только что вставленному результату,
		// иначе Make попытается добавить вторую маску к служебному слою.
		var resultLayerId = lr.getProperty("layerID");
		try { doc.makeSelectionFromLayer("mask", selection.junk); }
		catch (_) { doc.makeSelection(target); }
		if (!doc.hasProperty("selection")) doc.makeSelection(target);
		doc.selectLayersByIDs([resultLayerId]);
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
	function checkSelection(res) {
		if (!apl.getProperty("numberOfDocuments")) return;
		var quickMaskActive = !!doc.getProperty("quickMask"),
			quickMaskHadSelection = quickMaskActive && doc.hasProperty("selection"),
			quickMaskOuterBounds = quickMaskHadSelection
				? doc.descToObject(doc.getProperty("selection").value)
				: null;
		// Inpaint включается только когда Quick Mask была активна и в ней
		// существовало непустое выделение. Границы области генерации при этом
		// фиксируются ДО выхода из Quick Mask: это внешнее выделение, внутри
		// которого пользователь рисовал маску. После clearEvent текущая
		// Photoshop selection уже описывает саму маску и нужна только для
		// создания временного user mask в prepareSelectionLayer().
		//
		// Если Quick Mask была пустой, после выхода используем обычную
		// selection и работаем как обычный img2img, без inpaint.
		if (quickMaskActive) doc.quickMask("clearEvent");
		if (doc.hasProperty("selection")) {
			res.result = true;
			res.inpaint = quickMaskHadSelection;
			res.bounds = quickMaskOuterBounds ||
				doc.descToObject(doc.getProperty("selection").value);
			fitSelectionBounds(res, 1);
			return;
		}
		if (isGeneratedLayerName(lr.getProperty("name"))) {
			doc.makeSelectionFromLayer("transparencyEnum");
			if (doc.hasProperty("selection")) {
				res.result = true;
				res.bounds = doc.descToObject(doc.getProperty("selection").value);
				res.previousGeneration = lr.getProperty("layerID");
			}
			doc.deselect();
			if (res.result) fitSelectionBounds(res, 1);
		}
	}
	function placeResultHistory() {
		if (!placementResultFile || !placementSelection) throw new Error(str.errNoResult);
		generatedImageToLayer(placementResultFile, placementSelection);
	}
	this.run = run;
	this.placeResultHistory = placeResultHistory;
	this.isSeedControl = isSeedControl;
	this.makeRandomSeed = makeRandomUiSeed;
	this.prepareSelectionLayer = prepareSelectionLayer;
	this.checkSelection = checkSelection;
}
// ---
// PHOTOSHOP ACTIONS
// Разделяет обычный DESC и параметры конкретного Action. Общие библиотеки
// prompt/reference и настройки Python остаются в DESC, чтобы не размножаться
// и не устаревать внутри записанных Actions.
// ---
function ActionRuntime() {
	function saveSharedSettings() {
		if (!globalSettings) return;
		cfg.copySharedLibrariesTo(globalSettings);
		cfg.copyPythonRuntimeSettingsTo(globalSettings);
		globalSettings.save();
	}
	this.getPlaybackParameterCount = function () {
		try { return app.playbackParameters ? app.playbackParameters.count : 0; }
		catch (_) { return 0; }
	};
	this.isPlayback = function () {
		try {
			var desc = app.playbackParameters,
				marker = s2t("actionDataVersion");
			return !!(desc && desc.hasKey(marker));
		} catch (_) { return false; }
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
			var desc = app.playbackParameters,
				key = s2t("recordSettingsToAction");
			return !!(desc && desc.hasKey(key) && desc.getType(key) == DescValueType.BOOLEANTYPE && desc.getBoolean(key));
		} catch (_) { return false; }
	};
	// При playback с записанными параметрами профиль остаётся в Action,
	// а изменённые общие библиотеки отдельно синхронизируются с DESC.
	this.saveAcceptedSettings = function () {
		if (actionPlaybackMode && actionUsesRecordedSettings && cfg.recordSettingsToAction) {
			cfg.saveToAction();
			saveSharedSettings();
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
				(saveError && saveError.line ? " (" + str.jsxLine + saveError.line + ")" : "");
		}
	};
}
// ---
// СОСТОЯНИЕ BACKEND И ЗАГРУЗКА SCHEMA
// Скрывает различия Comfy/Forge: доступность, каталоги, профили, анализ workflow
// и гидратацию Forge-схем актуальными списками моделей/модулей.
// ---
function BackendRuntime() {
	var status = { mode: "none", backends: { comfy: { available: false }, forge: { available: false } } },
		pendingNotices = [],
		localComfyInputFolder = "",
		pythonApiVersion = "";
	function pushNotice(key, msg) {
		key = String(key || msg || "");
		msg = String(msg || "");
		if (!msg) return;
		for (var i = 0; i < pendingNotices.length; i++)
			if (String(pendingNotices[i].key) == key) return;
		pendingNotices.push({ key: key, message: msg });
	}
	function takeNotices() {
		var res = pendingNotices;
		pendingNotices = [];
		return res;
	}
	function quotedValue(value) {
		return "“" + String(value === undefined || value === null ? "" : value) + "”";
	}
	function replacementNotice(key, label, previous, replacement) {
		pushNotice(
			key,
			String(label) + ": " + quotedValue(previous) + " → " + quotedValue(replacement)
		);
	}
	function emptyNotice(key, label) {
		pushNotice(key, String(label) + ": " + str.noAvailableValues);
	}
	function applyStatus(response) {
		if (!response || typeof response != "object") return;
		if (response.hasOwnProperty("version"))
			pythonApiVersion = String(response.version || "");
		if (response.hasOwnProperty("comfy_input_folder"))
			localComfyInputFolder = String(response.comfy_input_folder || "");
		var source = response.backends ? response : { mode: "none", backends: {} };
		status = {
			mode: source.mode || "none",
			backends: {
				comfy: source.backends && source.backends.comfy ? source.backends.comfy : { available: false },
				forge: source.backends && source.backends.forge ? source.backends.forge : { available: false }
			}
		};
		if (!status.backends.comfy.available && status.backends.comfy.checked !== false)
			localComfyInputFolder = "";
	}
	function isAvailable(name) {
		return !!(status && status.backends && status.backends[name] && status.backends[name].available);
	}
	function statusLabel(value) {
		value = value || status;
		if (!value || value.mode == "none") return str.backendsNone;
		if (value.mode == "both") return "ComfyUI + Forge Neo";
		return value.mode == BACKEND_COMFY ? "ComfyUI" : "Forge Neo";
	}
	function normalizeActiveBackend() {
		var previous = cfg.activeBackend;
		if (isAvailable(previous)) return false;
		if (isAvailable(BACKEND_COMFY)) cfg.activeBackend = cfg.data.activeBackend = BACKEND_COMFY;
		else if (isAvailable(BACKEND_FORGE)) cfg.activeBackend = cfg.data.activeBackend = BACKEND_FORGE;
		return cfg.activeBackend != previous;
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
		var folderPath = cfg.forgeSchemasFolder || "",
			configuredFolder = folderPath ? new Folder(folderPath) : null;
		// Существующая выбранная папка передаётся Python даже тогда, когда в
		// ней нет ни одной корректной схемы. Иначе повреждённые JSON не доходят
		// до валидатора и пользователь видит только исчезнувший список.
		if (!configuredFolder || !configuredFolder.exists) folderPath = defaultForgeFolder();
		if (!folderPath && promptUser) {
			var sel = Folder.selectDialog(str.selectForgeSchemaFolder);
			if (sel) folderPath = sel.fsName;
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
	function findItem(items, itemId) {
		for (var i = 0; i < items.length; i++) if (items[i].id == itemId) return items[i];
		return null;
	}
	function chooseItem(items, selectedId, label, itemLabel, noticePrefix) {
		var sel = findItem(items, selectedId);
		if (sel) return selectedId;
		if (!items.length) {
			emptyNotice(noticePrefix + ":empty", label);
			return "";
		}
		var fallback = items[0], fallbackId = fallback.id;
		if (selectedId !== undefined && selectedId !== null && String(selectedId) !== "")
			replacementNotice(
				noticePrefix + ":missing:" + String(selectedId),
				label,
				selectedId,
				itemLabel(fallback)
			);
		return fallbackId;
	}
	function workflowLabel(item) {
		return String(item && (item.relative_path || item.name || item.id) || "");
	}
	function forgeSchemaLabel(item) {
		return String(item && (item.label || item.id) || "");
	}
	function chooseWorkflow(workflows) {
		return chooseItem(workflows, cfg.selectedWorkflow, str.workflow, workflowLabel, "workflow");
	}
	function findWorkflow(workflows, workflowId) { return findItem(workflows, workflowId); }
	function fastWorkflowSelection() {
		var workflowId = String(cfg.selectedWorkflow || ""),
			items = cfg.workflowCatalog instanceof Array ? cfg.workflowCatalog : [],
			selected = findWorkflow(items, workflowId),
			profile = workflowId ? cfg.getProfile(workflowId) : null,
			relativePath = String(selected && selected.relative_path || profile && profile.relativePath || "");
		if (!workflowId || !relativePath || !comfyFolderReady()) return null;
		var file = new File(cfg.workflowsFolder + "/" + relativePath);
		if (!file.exists) return null;
		if (!selected) {
			selected = { id: workflowId, name: file.displayName || file.name, relative_path: relativePath };
			// Каталоги не записываются в Action, поэтому для его тихого запуска
			// достаточно единственного синтетического элемента выбранного файла.
			items = [selected];
		}
		return { items: items, selected: selected };
	}
	function refreshForgeSchemas(progress) {
		if (!ensureForgeFolder(true)) {
			cfg.forgeCatalog = cfg.data.forgeCatalog = [];
			return [];
		}
		var response = api.forgeSchemaList(progress), items = response.items || [],
			invalid = response.invalid_schemas instanceof Array ? response.invalid_schemas : [];
		if (response.folder) cfg.forgeSchemasFolder = cfg.data.forgeSchemasFolder = String(response.folder);
		for (var i = 0; i < invalid.length; i++) {
			var fileName = String(invalid[i].file || str.unknownFile),
				msg = invalid[i] && invalid[i].code
					? apiErrorText(invalid[i])
					: String(invalid[i].message || str.invalidForgeSchema),
				displayMessage = invalid[i] && invalid[i].code
					? msg
					: str.invalidForgeSchema + " " + fileName + ": " + msg;
			pushNotice(
				"forge-schema-invalid:" + fileName + ":" + msg,
				displayMessage
			);
		}
		cfg.forgeCatalog = cfg.data.forgeCatalog = items;
		return items;
	}
	function chooseForgeSchema(items) {
		var selected = findForgeSchema(items, cfg.selectedForgePreset);
		if (selected) return selected.id;
		return chooseItem(items, cfg.selectedForgePreset, str.uiPreset, forgeSchemaLabel, "forge-preset");
	}
	// Runtime identity is always the JSON filename supplied as item.id.
	// Copies with the same internal schema id therefore have separate profiles.
	function findForgeSchema(items, presetId) { return findItem(items, presetId); }
	function fastForgeSelection() {
		var presetId = String(cfg.selectedForgePreset || ""),
			items = cfg.forgeCatalog instanceof Array ? cfg.forgeCatalog : [],
			selected = findForgeSchema(items, presetId);
		if (!presetId || !ensureForgeFolder(false)) return null;
		var fileName = String(selected && selected.file || presetId + ".json"),
			file = new File(cfg.forgeSchemasFolder + "/" + fileName);
		if (!file.exists) return null;
		if (!selected) {
			selected = { id: presetId, label: presetId, file: fileName };
			items = [selected];
		}
		return { items: items, selected: selected };
	}
	function forgeLoraNoticeList(names) {
		var parts = [];
		for (var i = 0; i < names.length; i++) parts.push(String(names[i] || ""));
		return parts.join(", ");
	}
	function applyForgeSchemaLoraDefaults(schema, catalog) {
		if (!schema) return;
		var profile = schemaProfile(schema),
			available = catalog && catalog.loras instanceof Array ? catalog.loras : [],
			source = [],
			validation;
		if (profile.lorasInitialized !== true) {
			source = hasDeclaredForgeLoras(schema) ? schema.loras : (profile.selectedLoras || []);
			validation = resolveForgeLoraSelection(available, source, true);
			profile.selectedLoras = validation.selected;
			profile.lorasInitialized = true;
			if (validation.missing.length)
				pushNotice(
					"forge-schema-default-loras-missing:" + forgeSchemaId(schema) + ":" + validation.missing.join("|"),
					str.forgeDefaultLorasMissing + forgeLoraNoticeList(validation.missing)
				);
			return;
		}
		validation = resolveForgeLoraSelection(available, profile.selectedLoras || [], true);
		profile.selectedLoras = validation.selected;
		if (validation.missing.length)
			pushNotice(
				"forge-profile-loras-missing:" + forgeSchemaId(schema) + ":" + validation.missing.join("|"),
				str.forgeSavedLorasMissing + forgeLoraNoticeList(validation.missing)
			);
	}
	function hydrateForgeSchema(schema, catalog) {
		schema = cloneObj(schema || {});
		schema.backend = BACKEND_FORGE;
		catalog = catalog || {};
		var controls = schema.controls || [];
		for (var i = 0; i < controls.length; i++) {
			var control = controls[i], source = control.source,
				id = String(control.id || ""), payloadKey = String(control.payload_key || "");
			if (source && catalog[source] instanceof Array) control.items = cloneObj(catalog[source]);
			control.backend = BACKEND_FORGE;
			// Каталог LoRA нужен только prompt-контролу. Раньше один и тот же
			// потенциально большой массив клонировался в каждый control схемы.
			if (id == "positive_prompt" || payloadKey == "prompt")
				control.forgeLoras = cloneObj(catalog.loras instanceof Array ? catalog.loras : []);
		}
		return schema;
	}
	function mergeCatalog(base, update) {
		var res = cloneObj(base || {}), key;
		update = update || {};
		for (key in update) if (update.hasOwnProperty(key)) res[key] = cloneObj(update[key]);
		return res;
	}
	function requiredForgeSources(schema) {
		if (!schema) return [];
		var profile = schemaProfile(schema),
			visible = resolveForgeVisibleControls(schema, profile),
			controls = schema.controls instanceof Array ? schema.controls : [],
			res = [], seen = {};
		function add(source) {
			source = String(source || "");
			if (!source || seen[source]) return;
			seen[source] = true;
			res.push(source);
		}
		for (var i = 0; i < controls.length; i++) {
			var control = controls[i], id = String(control.id || ""),
				shown = !!control.required_visible || arrayContains(visible, id);
			if (!shown) continue;
			if (control.source) add(control.source);
			if (id == "positive_prompt") add("loras");
		}
		if (hasDeclaredForgeLoras(schema) || (profile.selectedLoras instanceof Array && profile.selectedLoras.length))
			add("loras");
		return res;
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
			nextCatalog = ensureForgeCatalog(raw, catalog || {}, progress, !!forceCatalog),
			hydrated = hydrateForgeSchema(raw, nextCatalog);
		applyForgeSchemaLoraDefaults(hydrated, nextCatalog);
		return { catalog: nextCatalog, schema: hydrated };
	}
	var workflowAnalysisArgs = null,
		workflowAnalysisResult = null,
		workflowAnalysisCancelled = false;
	function schemaBackend(schema) {
		return schema && schema.backend == BACKEND_FORGE ? BACKEND_FORGE : BACKEND_COMFY;
	}
	function schemaProfile(schema) {
		if (schemaBackend(schema) == BACKEND_FORGE)
			return cfg.getForgeProfile(
				schema.workspace_id || String(schema.workflow_id || "").replace(/^forge:/, "")
			);
		return cfg.getProfile(schema.workflow_id);
	}
	function validateSchemaSelections(schema, profile) {
		var res = { notices: [], emptyDropdownIds: [] };
		if (!schema || !profile) return res;
		var currentBackend = schemaBackend(schema),
			visible = currentBackend == BACKEND_FORGE
				? resolveForgeVisibleControls(schema, profile)
				: profile.visibleControls,
			cleanForgeProfile = currentBackend == BACKEND_FORGE,
			storedKey;
		if (cleanForgeProfile)
			for (storedKey in profile.values)
				if (profile.values.hasOwnProperty(storedKey)) { cleanForgeProfile = false; break; }
		if (visible === null || visible === undefined) visible = schema.recommended_controls || [];
		var controls = schema.controls instanceof Array ? schema.controls : [],
			schemaId = String(schema.workflow_id || schema.workspace_id || "schema");
		for (var i = 0; i < controls.length; i++) {
			var definition = controls[i], id = String(definition.id || "");
			if (!id || definition.type != "dropdown" || !arrayContains(visible, id)) continue;
			var items = definition.items instanceof Array ? definition.items : [],
				label = String(ui.label(definition) || id),
				key = "dropdown:" + schemaId + ":" + id;
			if (!items.length) {
				res.emptyDropdownIds.push(id);
				res.notices.push({ key: key + ":empty", level: "error", message: label + ": " + str.noAvailableValues });
				continue;
			}
			var hasStored = profile.values.hasOwnProperty(id),
				cur = hasStored ? profile.values[id] : definition.value,
				found = false;
			for (var j = 0; j < items.length; j++)
				if (String(itemData(items[j]).value) == String(cur)) { found = true; break; }
			if (found) continue;
			var replacement = itemData(items[0]),
				previous = cur === undefined || cur === null ? "" : cur,
				emptySchemaCheckpoint = cleanForgeProfile && !hasStored &&
					startsWithSemantic(id, "checkpoint") &&
					String(previous).replace(/^\s+|\s+$/g, "") == "";
			profile.values[id] = cloneObj(replacement.value);
			// Для нового или полностью сброшенного Forge-профиля пустой
			// checkpoint в JSON означает «использовать первый доступный».
			// Это начальная инициализация, а не потеря пользовательского
			// значения, поэтому предупреждение и принудительный диалог не нужны.
			if (emptySchemaCheckpoint) continue;
			res.notices.push({
				key: key + ":missing:" + String(previous),
				message: label + ": " + quotedValue(previous) + " → " + quotedValue(replacement.label)
			});
		}
		return res;
	}
	function profileValues(schema, profile) {
		var res = {},
			currentBackend = schemaBackend(schema),
			visible = currentBackend == BACKEND_FORGE
				? resolveForgeVisibleControls(schema, profile)
				: profile.visibleControls;
		if (visible === null || visible === undefined) visible = schema.recommended_controls || [];
		var controls = schema.controls || [];
		for (var i = 0; i < controls.length; i++) {
			var definition = controls[i],
				isVisible = arrayContains(visible, definition.id);
			if (currentBackend != BACKEND_FORGE && !isVisible) continue;
			var sourceValue = isVisible && profile.values.hasOwnProperty(definition.id)
				? cloneObj(profile.values[definition.id])
				: cloneObj(definition.value);
			if (definition.type == "multiselect") {
				sourceValue = ui.normalizeMultiselect(definition, sourceValue);
				if (isVisible && profile.values.hasOwnProperty(definition.id))
					profile.values[definition.id] = cloneObj(sourceValue);
			}
			res[definition.id] = sourceValue;
		}
		if (currentBackend == BACKEND_FORGE && schema.capabilities && schema.capabilities.image_stitch) {
			var stitchVisible = arrayContains(visible, "image_stitch");
			res.image_stitch = stitchVisible && profile.values.hasOwnProperty("image_stitch")
				? toBooleanValue(profile.values.image_stitch)
				: toBooleanValue(schema.image_stitch_default);
		}
		return res;
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
		workflowAnalysisCancelled = false;
		var progressCompleted;
		try {
			progressCompleted = app.doProgress(str.progressAnalyze, "runWorkflowAnalysisProgress()");
		} catch (progressError) {
			if (!isUserCancellation(progressError)) throw progressError;
			progressCompleted = false;
		}
		var res = workflowAnalysisResult,
			wasCancelled = workflowAnalysisCancelled;
		workflowAnalysisArgs = null;
		workflowAnalysisResult = null;
		workflowAnalysisCancelled = false;
		if (progressCompleted === false || wasCancelled) throw userCancellationError();
		if (!res) throw new Error(str.errEmptyApiAnswer);
		return res;
	}
	function runWorkflowAnalysisProgress() {
		if (!app.doProgressSegmentTask(100, 0, 100, "workflowAnalysisStage()")) {
			workflowAnalysisCancelled = true;
			return false;
		}
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
	this.getStatus = function () {
		var snapshot = cloneObj(status);
		snapshot.comfy_input_folder = localComfyInputFolder;
		return snapshot;
	};
	this.comfyUploadFolder = function () {
		if (!localComfyInputFolder) return "";
		return new Folder(localComfyInputFolder + "/" + APP.comfyUploadSubfolder).fsName;
	};
	this.hasAvailable = function () { return status.mode != "none"; };
	this.isAvailable = isAvailable;
	this.statusLabel = statusLabel;
	this.pythonVersion = function () { return pythonApiVersion; };
	this.normalizeActiveBackend = normalizeActiveBackend;
	this.comfyFolderReady = comfyFolderReady;
	this.defaultForgeFolder = defaultForgeFolder;
	this.forgeFolderReady = function () { return ensureForgeFolder(false); };
	this.refreshWorkflows = refreshWorkflows;
	this.takeNotices = takeNotices;
	this.validateSchemaSelections = validateSchemaSelections;
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
	function finalizeInitialData(res) {
		var validation = res.schema
			? validateSchemaSelections(res.schema, schemaProfile(res.schema))
			: { notices: [], emptyDropdownIds: [] },
			informationProfile = res.schema && schemaBackend(res.schema) == BACKEND_COMFY
				? schemaProfile(res.schema)
				: null,
			informationPending = !!(informationProfile &&
				informationProfile.workflowInformationSeen !== true &&
				schemaHasInformationalDiagnostics(res.schema));
		res.notices = takeNotices().concat(validation.notices);
		res.emptyDropdownIds = validation.emptyDropdownIds;
		res.forceDialog = !!(res.notices.length || res.emptyDropdownIds.length ||
			(res.schema && !res.schema.valid) || informationPending);
		return res;
	}
	this.loadInitialData = function (progress, allowFastPath) {
		var res = { backend: cfg.activeBackend, workflows: [], forgePresets: [], forgeCatalog: null, schema: null, fastPath: false },
			requireRecordedSelection = actionPlaybackMode && actionUsesRecordedSettings;
		if (cfg.activeBackend == BACKEND_FORGE) {
			var fastForge = allowFastPath ? fastForgeSelection() : null;
			if (fastForge) {
				res.forgePresets = fastForge.items;
				res.fastPath = true;
			} else {
				if (progress) progress.setStage(str.progressForgePresets, 42);
				res.forgePresets = refreshForgeSchemas(progress);
				if (requireRecordedSelection) {
					if (!findForgeSchema(res.forgePresets, cfg.selectedForgePreset))
						throw new Error(apiErrorText({ code: "forge_schema_missing", params: [cfg.selectedForgePreset] }));
				} else cfg.selectedForgePreset = cfg.data.selectedForgePreset = chooseForgeSchema(res.forgePresets);
			}
			res.forgeCatalog = {};
			if (cfg.selectedForgePreset) {
				var loadedForge;
				try {
					loadedForge = loadForgeSchema(cfg.selectedForgePreset, res.forgeCatalog, progress, false);
				} catch (fastForgeError) {
					if (!res.fastPath || requireRecordedSelection) throw fastForgeError;
					// Повреждённая или удалённая выбранная схема не должна
					// превращать тихий запуск в тупиковую ошибку: полный список
					// найдёт валидную замену и откроет окно с уведомлением.
					if (progress) progress.setStage(str.progressForgePresets, 42);
					res.forgePresets = refreshForgeSchemas(progress);
					if (findForgeSchema(res.forgePresets, cfg.selectedForgePreset)) throw fastForgeError;
					cfg.selectedForgePreset = cfg.data.selectedForgePreset = chooseForgeSchema(res.forgePresets);
					res.fastPath = false;
					if (cfg.selectedForgePreset)
						loadedForge = loadForgeSchema(cfg.selectedForgePreset, res.forgeCatalog, progress, false);
				}
				if (loadedForge) {
					res.forgeCatalog = loadedForge.catalog;
					res.schema = loadedForge.schema;
				}
			}
			return finalizeInitialData(res);
		}
		if (!comfyFolderReady()) {
			if (requireRecordedSelection) {
				if (!cfg.workflowsFolder) throw new Error(apiErrorText({ code: "workflow_folder_not_selected" }));
				throw new Error(apiErrorText({ code: "workflow_folder_missing", params: [cfg.workflowsFolder] }));
			}
			cfg.selectedWorkflow = cfg.data.selectedWorkflow = "";
			return finalizeInitialData(res);
		}
		var fastWorkflow = allowFastPath ? fastWorkflowSelection() : null;
		if (fastWorkflow) {
			res.workflows = fastWorkflow.items;
			res.fastPath = true;
		} else {
			if (progress) progress.setStage(str.progressWorkflows, 42);
			res.workflows = refreshWorkflows(progress);
			if (requireRecordedSelection) {
				if (!findWorkflow(res.workflows, cfg.selectedWorkflow))
					throw new Error(apiErrorText({ code: "selected_workflow_missing" }));
			} else cfg.selectedWorkflow = cfg.data.selectedWorkflow = chooseWorkflow(res.workflows);
		}
		if (res.workflows.length) {
			var sel = findWorkflow(res.workflows, cfg.selectedWorkflow);
			if (!sel) throw new Error(str.errSelectedWorkflowMissing);
			var profile = cfg.getProfile(sel.id);
			profile.relativePath = sel.relative_path || profile.relativePath || "";
			res.schema = cfg.getCachedSchema(sel.id, sel);
			if (!res.schema) {
				if (!startupProgress) startupProgress = ui.createDelayedStartupProgress(str.progressAnalyze, ANALYZE_TIMEOUT, STARTUP_PROGRESS_DELAY);
				if (progress) progress.setStage(str.progressAnalyze, 63);
				res.schema = analyzeWorkflow(sel, profile, false, progress);
				cfg.cacheSchema(res.schema, sel);
			}
		}
		return finalizeInitialData(res);
	};
}
// ---
// ЕДИНЫЙ ДИСПЕТЧЕР ПОЛЬЗОВАТЕЛЬСКИХ СООБЩЕНИЙ
// Ошибки, предупреждения, информация, подтверждения и ввод текста используют
// только кастомные ScriptUI-окна. Звук Photoshop подаётся только для ошибок.
// ---
function MessageCenter() {
	function stripAppHeader(text) {
		var normalized = String(text || "").replace(/\r\n?/g, "\n"),
			lineEnd = normalized.indexOf("\n"),
			firstLine = lineEnd < 0 ? normalized : normalized.substring(0, lineEnd);
		if (firstLine.replace(/^\s+|\s+$/g, "").toLowerCase() != APP.name.toLowerCase()) return normalized;
		if (lineEnd < 0) return "";
		return normalized.substring(lineEnd + 1).replace(/^(?:[ \t]*\n)+/, "");
	}
	function unique(items, stripRedundantAppHeader) {
		var res = [], seen = {};
		items = items instanceof Array ? items : [];
		for (var i = 0; i < items.length; i++) {
			var text = errorMessageText(items[i]);
			if (stripRedundantAppHeader) text = stripAppHeader(text);
			text = text.replace(/^\s+|\s+$/g, "");
			var key = text.toLowerCase();
			if (!text || seen[key]) continue;
			seen[key] = true;
			res.push(text);
		}
		return res;
	}
	this.show = function (options) {
		options = options || {};
		var errors = unique(options.errors, true),
			warnings = unique(options.warnings),
			information = unique(options.information);
		if (!errors.length && !warnings.length && !information.length) return false;
		var sections = [];
		if (errors.length) sections.push(str.diagnosticsErrors + "\n• " + errors.join("\n• "));
		if (warnings.length) sections.push(str.diagnosticsWarnings + "\n• " + warnings.join("\n• "));
		if (information.length) sections.push(str.diagnosticsInformation + ":\n• " + information.join("\n• "));
		var dialog = ui.createDialog({ title: options.title || APP.name, spacing: 10, margins: 15 }),
			heading = dialog.add("statictext", undefined, errors.length ? str.diagnosticsNeedAttention : str.diagnosticsInformation),
			details = dialog.add("edittext", undefined, sections.join("\n\n"), { multiline: true, scrollable: true, readonly: true }),
			buttons = dialog.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,5,0,0]}");
		buttons.add("button", undefined, str.dialogOk, { name: "ok" });
		try { heading.graphics.font = ScriptUI.newFont(heading.graphics.font.name, "BOLD", 15); } catch (_) { }
		var itemCount = errors.length + warnings.length + information.length;
		details.preferredSize = [700, Math.min(380, Math.max(180, 90 + itemCount * 28))];
		details.minimumSize = [540, 180];
		details.readonly = true;
		if (errors.length && options.sound !== false) try { app.beep(); } catch (_) { }
		ui.showDialog(dialog);
		return true;
	};
	this.error = function (value, title) {
		return this.show({ title: title || APP.name, errors: [errorMessageText(value)] });
	};
	this.confirm = function (message, title, yesText, noText) {
		var result = null,
			dialog = ui.createDialog({ title: title || APP.name, spacing: 12, margins: 15 }),
			text = ui.addMultilineNote(dialog, message, 500),
			buttons = dialog.add("group{orientation:'row',alignment:['right','top'],alignChildren:['right','center'],spacing:8,margins:0}"),
			noButton = buttons.add("button", undefined, noText || str.dialogNo, { name: "cancel" }),
			yesButton = buttons.add("button", undefined, yesText || str.dialogYes, { name: "ok" });
		text.preferredSize.width = text.minimumSize.width = 500;
		noButton.onClick = function () { result = false; dialog.close(1); };
		yesButton.onClick = function () { result = true; dialog.close(1); };
		try { dialog.cancelElement = null; } catch (_) { }
		try {
			dialog.addEventListener("keydown", function (event) {
				if (!event || event.keyName != "Escape") return;
				result = null;
				try { event.preventDefault(); } catch (_) { }
				try { event.stopPropagation(); } catch (_) { }
				dialog.close(0);
			}, true);
		} catch (_) { }
		dialog.onShow = function () {
			try { dialog.cancelElement = null; } catch (_) { }
			try { dialog.defaultElement = noButton; } catch (_) { }
			try { noButton.active = true; } catch (_) { }
		};
		ui.showDialog(dialog);
		return result;
	};
	this.prompt = function (message, defaultValue, title) {
		var result = null,
			dialog = ui.createDialog({ title: title || APP.name, spacing: 10, margins: 15 }),
			text = ui.addMultilineNote(dialog, message, 500),
			input = dialog.add("edittext", undefined, String(defaultValue === undefined ? "" : defaultValue)),
			buttons = dialog.add("group{orientation:'row',alignment:['right','top'],alignChildren:['right','center'],spacing:8,margins:[0,5,0,0]}"),
			cancelButton = buttons.add("button", undefined, str.dialogCancel, { name: "cancel" }),
			okButton = buttons.add("button", undefined, str.dialogOk, { name: "ok" });
		text.preferredSize.width = text.minimumSize.width = 500;
		input.preferredSize.width = input.minimumSize.width = 500;
		cancelButton.onClick = function () { result = null; dialog.close(0); };
		okButton.onClick = function () { result = input.text; dialog.close(1); };
		dialog.onShow = function () {
			try { dialog.defaultElement = okButton; } catch (_) { }
			try { input.active = true; } catch (_) { }
		};
		ui.showDialog(dialog);
		return result;
	};
}
// ---
// БИБЛИОТЕКА КОМПОНЕНТОВ SCRIPTUI
// Фабрики возвращают контроллеры с getValue(), но сами живые ScriptUI-элементы
// никогда не переносятся между окнами или пересозданными контейнерами.
// ---
function UI() {
	var self = this;
	this.mainWindowWidth = 360;
	this.settingsControlWidth = 385;
	this.presetButtonWidth = 27;
	this.mainSettingsButtonWidth = 27;
	this.loadMetadataButtonWidth = 54;
	this.sliderValueWidth = 65;
	this.autoResizeCheckboxWidth = 20;
	function itemValue(item) {
		return item && item.controlValue !== undefined ? item.controlValue : (item ? item.text : "");
	}
	function populate(control, items) {
		items = items instanceof Array ? items : [];
		for (var i = 0; i < items.length; i++) {
			var data = itemData(items[i]), item = control.add("item", data.label);
			item.controlValue = data.value;
			item.itemHelp = data.help;
		}
		return control;
	}
	function read(control, multiselect) {
		if (!multiselect) return control.selection ? itemValue(control.selection) : "";
		var res = [], selection = control.selection;
		if (!selection) return res;
		if (!(selection instanceof Array)) selection = [selection];
		for (var i = 0; i < selection.length; i++) res.push(itemValue(selection[i]));
		return res;
	}
	function restore(control, savedValue, multiselect, fallbackIndex) {
		if (multiselect) {
			var selectedValues = savedValue instanceof Array ? savedValue : [];
			for (var i = 0; i < control.items.length; i++) control.items[i].selected = arrayContains(selectedValues, itemValue(control.items[i]));
			return read(control, true);
		}
		var sel = null;
		for (var j = 0; j < control.items.length; j++) {
			if (String(itemValue(control.items[j])) == String(savedValue)) { sel = control.items[j]; break; }
		}
		if (!sel && fallbackIndex !== undefined && control.items.length) {
			var index = Math.max(0, Math.min(control.items.length - 1, Number(fallbackIndex) || 0));
			sel = control.items[index];
		}
		control.selection = sel || null;
		return control.selection;
	}
	this.contentWidth = function () {
		return Math.max(220, self.mainWindowWidth - 30);
	};
	this.headerTextWidth = function (hasMetadata) {
		return Math.max(100, self.contentWidth() - self.mainSettingsButtonWidth - (hasMetadata ? self.loadMetadataButtonWidth : 0));
	};
	this.setFixedWidth = function (control, width) {
		width = Math.max(0, Number(width) || 0);
		control.preferredSize.width = control.minimumSize.width = control.maximumSize.width = width;
		return control;
	};
	this.createDialog = function (options) {
		options = options || {};
		var spacing = options.spacing === undefined ? 8 : options.spacing,
			margins = options.margins === undefined ? 15 : options.margins,
			marginsText = margins instanceof Array ? "[" + margins.join(",") + "]" : margins,
			dialog = new Window(
				"dialog{orientation:'column',alignChildren:['fill','top'],spacing:" +
				spacing + ",margins:" + marginsText + "}"
			);
		dialog.text = options.title || APP.name;
		return dialog;
	};
	this.showDialog = function (dialog) {
		self.enableHoverFocus(dialog);
		dialog.center();
		return dialog.show();
	};
	this.addMultilineNote = function (parent, text, width, height) {
		var note = parent.add("statictext", undefined, text || "", { multiline: true });
		if (width || height) note.preferredSize = [width || -1, height || -1];
		return note;
	};
	this.addAcceptRow = function (parent, text, onClick) {
		var row = parent.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,10,0,0]}"),
			button = row.add("button", undefined, text, { name: "ok" });
		if (onClick) button.onClick = onClick;
		return button;
	};
	this.addVisibleControlsEditor = function (parent, options) {
		options = options || {};
		var panel = parent.add("panel{orientation:'column',alignChildren:['fill','top'],spacing:5,margins:10}");
		panel.text = options.title || "";
		var list = panel.add("listbox", undefined, [], { multiselect: true });
		list.preferredSize = [options.width || 520, options.height || 250];
		var controls = options.controls || [],
			visible = options.visible || [],
			recommendedIds = options.recommendedIds || [];
		for (var i = 0; i < controls.length; i++) {
			var definition = controls[i],
				required = options.isRequired ? !!options.isRequired(definition) : false,
				label = options.itemLabel ? options.itemLabel(definition, required) : String(definition.label || definition.id || ""),
				item = list.add("item", label);
			item.controlId = definition.id;
			item.requiredVisible = required;
			if (options.itemHelp) item.helpTip = String(options.itemHelp(definition, required) || "");
			item.selected = required || arrayContains(visible, definition.id);
			if (required) try { item.enabled = false; } catch (_) { }
		}
		var selectRow = panel.add("group{orientation:'row',alignChildren:['fill','center'],spacing:5,margins:0}"),
			recommended = selectRow.add("button", undefined, options.recommendedText),
			all = selectRow.add("button", undefined, options.allText),
			none = selectRow.add("button", undefined, options.noneText);
		function applySelection(mode) {
			for (var j = 0; j < list.items.length; j++) {
				var item = list.items[j];
				if (mode == "all") item.selected = true;
				else if (mode == "none") item.selected = !!item.requiredVisible;
				else item.selected = !!item.requiredVisible || arrayContains(recommendedIds, item.controlId);
			}
		}
		recommended.onClick = function () { applySelection("recommended"); };
		all.onClick = function () { applySelection("all"); };
		none.onClick = function () { applySelection("none"); };
		var fieldLabelWidth = 175,
			fieldControlWidth = 80,
			multipleRow = parent.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
			multipleTitle = multipleRow.add("statictext"),
			multiple = multipleRow.add("edittext"),
			outputFormat = null;
		self.setFixedWidth(multipleTitle, fieldLabelWidth);
		self.setFixedWidth(multiple, fieldControlWidth);
		multipleTitle.text = options.sizeLabel || "";
		multiple.text = String(options.sizeValue === undefined ? "" : options.sizeValue);
		if (options.outputFormatLabel) {
			var formatRow = parent.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
				formatTitle = formatRow.add("statictext");
			outputFormat = formatRow.add("dropdownlist");
			self.setFixedWidth(formatTitle, fieldLabelWidth);
			self.setFixedWidth(outputFormat, fieldControlWidth);
			formatTitle.text = options.outputFormatLabel;
			populate(outputFormat, options.outputFormatItems || []);
			restore(outputFormat, normalizeOutputFormat(options.outputFormatValue), false, 0);
		}
		return {
			list: list,
			multiple: multiple,
			outputFormat: outputFormat,
			selectedIds: function () {
				var res = [];
				for (var j = 0; j < list.items.length; j++)
					if (list.items[j].selected || list.items[j].requiredVisible) res.push(list.items[j].controlId);
				return res;
			}
		};
	};
	this.promptHeight = function () {
		return Math.max(54, 80 - Math.round(Math.max(0, self.mainWindowWidth - 315) * 0.4));
	};
	this.multiSelectHeight = 86;
	// Hover-focus нужен главным образом слайдерам: колесо/клавиши начинают
	// менять значение без предварительного клика. Для остальных типов attach
	// намеренно ничего не делает.
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
	this.addFormRows = function (parent, descriptions, totalWidth) {
		var res = {}, rows = descriptions instanceof Array ? descriptions : [];
		for (var i = 0; i < rows.length; i++) {
			var d = rows[i], row = parent.add("group{orientation:'row',alignChildren:['left','center'],spacing:5,margins:0}"),
				title = row.add("statictext"), control, button = null;
			if (totalWidth) self.setFixedWidth(row, totalWidth);
			if (d.labelWidth) title.preferredSize = [d.labelWidth, -1];
			title.text = d.label;
			control = d.type == "static" ? row.add("statictext") : row.add("edittext", undefined, "", d.readOnly ? { readonly: true } : {});
			if (d.controlWidth) control.preferredSize = [d.controlWidth, -1];
			if (d.justify) control.justify = d.justify;
			control.text = d.value === undefined || d.value === null ? "" : d.value;
			if (d.button) {
				button = row.add("button");
				button.preferredSize = [d.button.width || 25, d.button.height || 25];
				button.text = d.button.text || "";
				button.helpTip = d.button.helpTip || "";
			}
			res[d.id] = { row: row, title: title, control: control, button: button };
		}
		return res;
	};
	this.addCheckboxes = function (parent, descriptions) {
		var res = {}, rows = descriptions instanceof Array ? descriptions : [];
		for (var i = 0; i < rows.length; i++) {
			var d = rows[i], checkbox = parent.add("checkbox");
			checkbox.text = d.text;
			checkbox.value = toBooleanValue(d.value);
			res[d.id] = checkbox;
		}
		return res;
	};
	this.addDropdown = function (parent, labelText, items, preferredWidth, margins) {
		var group = self.addColumn(parent, margins || 0),
			controlWidth = preferredWidth || self.contentWidth();
		self.setFixedWidth(group, controlWidth);
		var title = group.add("statictext"), dropdown = group.add("dropdownlist{preferredSize:[" + controlWidth + ",-1]}");
		dropdown.minimumSize.width = dropdown.maximumSize.width = controlWidth;
		title.text = labelText;
		populate(dropdown, items || []);
		return { group: group, title: title, dropdown: dropdown };
	};
	this.selectDropdown = function (dropdown, value, fallback) { return restore(dropdown, value, false, fallback); };
	this.addSlider = function (parent, labelText, min, max, value, options) {
		options = options || {};
		var controlWidth = options.controlWidth || self.contentWidth(),
			valueWidth = options.valueWidth || self.sliderValueWidth,
			titleSpacing = options.titleSpacing === undefined ? 0 : options.titleSpacing,
			titleWidth = options.titleWidth || (controlWidth - valueWidth - titleSpacing),
			group = parent.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:" + (options.margins || 0) + "}");
		self.setFixedWidth(group, controlWidth);
		var titleGroup = group.add("group{orientation:'row',alignChildren:['left','center'],spacing:" + titleSpacing + ",margins:0}");
		self.setFixedWidth(titleGroup, controlWidth);
		var title = titleGroup.add("statictext{preferredSize:[" + titleWidth + ",-1]}"),
			valueText = titleGroup.add("statictext{preferredSize:[" + valueWidth + ",-1],justify:'right'}"),
			slider = group.add("slider{minvalue:" + min + ",maxvalue:" + max + "}");
		self.setFixedWidth(slider, controlWidth);
		title.text = labelText;
		slider.value = value;
		valueText.text = options.displayValue !== undefined ? options.displayValue : value;
		// Слайдер должен получать focus по hover сразу после создания. Это важно
		// для динамически добавленных контролов (например, LoRA после выбора).
		try { self.enableHoverFocus(slider); } catch (_) { }
		return { group: group, titleGroup: titleGroup, title: title, valueText: valueText, slider: slider };
	};
	this.normalizeMultiselect = function (schema, storedValue) {
		var items = schema && schema.items instanceof Array ? schema.items : [],
			savedValues = storedValue instanceof Array ? storedValue : [], res = [];
		for (var i = 0; i < items.length; i++) {
			var value = itemData(items[i]).value;
			for (var j = 0; j < savedValues.length; j++) {
				if (String(savedValues[j]) == String(value)) {
					if (!arrayContains(res, value)) res.push(value);
					break;
				}
			}
		}
		return res;
	};
	function semanticLabel(schema) {
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
		}, id = String(schema.id || ""), semantic = id.split("__")[0];
		return labels.hasOwnProperty(semantic) ? labels[semantic] : undefined;
	}
	function label(schema) {
		if (schema.display_label !== undefined) return String(schema.display_label || "");
		var id = String(schema.id || ""), semantic = id.split("__")[0], value = semanticLabel(schema);
		if (value === undefined) return schema.label;
		if (id != semantic && schema.label && String(schema.label).indexOf("—") >= 0)
			return value + " " + String(schema.label).substring(String(schema.label).indexOf("—"));
		return value;
	}
	function help(schema) {
		if (schema.display_help !== undefined) return String(schema.display_help || "");
		if (schema.help) return schema.help;
		if (schema.node_id !== undefined && schema.input !== undefined) return str.nodeInput + schema.node_id + ", " + schema.input;
		return schema.label || schema.id || "";
	}
	function prettyInputLabel(value) {
		var text = String(value || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ");
		text = text.replace(/^\s+|\s+$/g, "").replace(/\s+/g, " ");
		return text ? text.charAt(0).toUpperCase() + text.substring(1) : "";
	}
	function semanticNodeContext(schema) {
		var title = String(schema.node_title || ""), classType = String(schema.class_type || ""),
			source = (title + " " + classType).toLowerCase().replace(/[^a-z0-9]+/g, " ");
		// Только устойчивые семейства. Для custom nodes ниже остаётся обычный title/class fallback.
		if (/vae/.test(source)) return "VAE";
		if (/checkpoint|ckpt/.test(source)) return "Checkpoint";
		if (/clip|text encoder/.test(source)) return "CLIP";
		if (/unet|diffusion|(^| )dit(?: |loader|model)|transformer/.test(source)) return "Diffusion";
		if (/ksampler|k sampler/.test(source)) return "KSampler";
		if (/controlnet/.test(source)) return "ControlNet";
		if (/lora/.test(source)) return "LoRA";
		return "";
	}
	function compactNodeContext(schema) {
		var semantic = semanticNodeContext(schema);
		if (semantic) return semantic;
		var title = String(schema.node_title || "").replace(/^\s+|\s+$/g, ""),
			classType = String(schema.class_type || "").replace(/^\s+|\s+$/g, "");
		if (!title) title = prettyInputLabel(classType);
		// Версии в конце названия ноды полезны в tooltip, но редко различают поля.
		title = title.replace(/\s*\(\s*v?\d+(?:\.\d+)+(?:[^)]*)\)\s*$/i, "");
		var words = title.split(/\s+/), first = words[0];
		// У имён семейства вроде SeedVR2 первый токен обычно уже уникален.
		if (words.length > 2 && (/\d/.test(first) || /[a-z][A-Z]/.test(first))) title = first;
		else if (words.length > 3) title = words.slice(0, 3).join(" ");
		return title;
	}
	function isLowInformationInput(value) {
		var key = String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, ""),
			low = {
				value: true, type: true, device: true, active: true, enabled: true,
				seed: true, steps: true, strength: true, cfg: true, denoise: true,
				shift: true, guidance: true, sampler: true, scheduler: true,
				weight_dtype: true
			};
		return !!low[key];
	}
	this.compactLabelInfo = function (schema) {
		var semantic = semanticLabel(schema), input = String(schema.input || ""),
			base = semantic !== undefined ? String(semantic) : prettyInputLabel(input),
			valueOnly = semantic === undefined && String(input || "").toLowerCase() == "value",
			valueTitle = valueOnly ? String(schema.node_title || "").replace(/^\s+|\s+$/g, "").replace(/\s+/g, " ") : "";
		if (!base) base = String(schema.label || schema.id || "");
		return {
			base: base,
			context: compactNodeContext(schema),
			semantic: semantic !== undefined,
			lowInfo: semantic === undefined && isLowInformationInput(input),
			valueOnly: valueOnly,
			valueTitle: valueTitle
		};
	};
	this.compactMainHelp = function (schema, displayLabel) {
		var parts = [], originalHelp = schema.help ? String(schema.help) : "",
			originalLabel = String(schema.label || ""), technical = "";
		if (originalHelp) parts.push(originalHelp);
		if (originalLabel && originalLabel != displayLabel && originalLabel != originalHelp) parts.push(originalLabel);
		if (schema.node_id !== undefined && schema.input !== undefined)
			technical = str.nodeInput + schema.node_id + ", " + schema.input;
		if (technical && technical != originalHelp && technical != originalLabel) parts.push(technical);
		if (!parts.length) parts.push(originalLabel || String(schema.id || ""));
		return parts.join("\n");
	};
	this.showItemSelector = function (options) {
		options = options || {};
		var items = options.items instanceof Array ? options.items : [],
			multiselect = !!options.multiselect,
			selectedValues = options.selectedValues instanceof Array ? options.selectedValues : [],
			selectedMap = {},
			accepted = false,
			res = null,
			w = new Window("dialog{orientation:'column',alignChildren:['fill','top'],spacing:8,margins:15}"),
			search = w.add("edittext"),
			list = w.add("listbox", undefined, [], { multiselect: multiselect }),
			buttons = w.add("group{orientation:'row',alignChildren:['center','center'],spacing:10,margins:[0,5,0,0]}"),
			clear = multiselect ? buttons.add("button", undefined, str.none) : null,
			ok = buttons.add("button", undefined, "OK", { name: "ok" });
		function valueKey(value) { return typeof value + ":" + String(value); }
		function syncVisibleSelection() {
			if (!multiselect) return;
			var selection = list.selection;
			for (var i = 0; i < list.items.length; i++)
				selectedMap[valueKey(list.items[i].controlValue)] = false;
			if (!selection) return;
			if (!(selection instanceof Array)) selection = [selection];
			for (var j = 0; j < selection.length; j++)
				selectedMap[valueKey(selection[j].controlValue)] = true;
		}
		function clearSelection() {
			selectedMap = {};
			try { list.selection = null; } catch (_) { }
			for (var i = 0; i < list.items.length; i++)
				try { list.items[i].selected = false; } catch (_) { }
		}
		function rebuild(filter) {
			filter = String(filter || "").toLowerCase();
			list.removeAll();
			for (var i = 0; i < items.length; i++) {
				var data = itemData(items[i]);
				if (filter && data.label.toLowerCase().indexOf(filter) < 0) continue;
				var item = list.add("item", data.label);
				item.controlValue = data.value;
				if (multiselect) item.selected = !!selectedMap[valueKey(data.value)];
			}
			if (!multiselect && list.items.length) list.selection = 0;
			ok.enabled = multiselect || !!list.selection;
		}
		for (var i = 0; i < selectedValues.length; i++) selectedMap[valueKey(selectedValues[i])] = true;
		w.text = options.title || "";
		search.preferredSize = [520, -1];
		search.helpTip = options.searchHelp || "";
		list.preferredSize = [520, 320];
		rebuild("");
		search.onChanging = function () { syncVisibleSelection(); rebuild(this.text); };
		list.onChange = function () {
			if (multiselect) syncVisibleSelection();
			else ok.enabled = !!this.selection;
		};
		if (clear) clear.onClick = function () { clearSelection(); };
		function acceptSelection() {
			if (multiselect) {
				syncVisibleSelection();
				res = [];
				for (var i = 0; i < items.length; i++) {
					var data = itemData(items[i]);
					if (selectedMap[valueKey(data.value)]) res.push(data.value);
				}
			} else {
				if (!list.selection) return;
				res = itemValue(list.selection);
			}
			accepted = true;
			w.close(1);
		}
		list.onDoubleClick = function () {
			var selection = this.selection;
			if (!selection) return;
			if (multiselect && selection instanceof Array && selection.length != 1) return;
			acceptSelection();
		};
		ok.onClick = acceptSelection;
		ui.enableHoverFocus(w);
		w.center();
		w.show();
		return accepted ? res : null;
	};
	function isModuleMultiSelect(schema) {
		if (!schema || schema.type != "multiselect") return false;
		var id = String(schema.id || "");
		return id == "modules" || startsWithSemantic(id, "vae") || startsWithSemantic(id, "text_encoder");
	}
	function addSelectorHost(parent, width, titleText, titleHelp, selectHelp, enabled) {
		var group = self.addColumn(parent, 0, "top"),
			header = group.add("group{orientation:'row',alignChildren:['left','center'],spacing:0,margins:0}"),
			title = header.add("statictext"),
			add = header.add("button"),
			selectedHost = group.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:0}");
		self.setFixedWidth(group, width);
		self.setFixedWidth(header, width);
		title.alignment = ["fill", "center"];
		title.minimumSize.width = 0;
		title.text = titleText || "";
		title.helpTip = titleHelp || "";
		add.alignment = ["right", "center"];
		self.setFixedWidth(add, self.presetButtonWidth);
		add.text = str.presetAddButton;
		add.helpTip = selectHelp || "";
		add.enabled = !!enabled;
		self.setFixedWidth(selectedHost, width);
		return {
			group: group,
			add: add,
			selectedHost: selectedHost,
			relayout: function () {
				try { selectedHost.layout.layout(true); } catch (_) { }
				try { group.layout.layout(true); } catch (_) { }
				try {
					var owner = group.window || group;
					while (owner && owner.parent) owner = owner.parent;
					if (owner && owner.layout) {
						owner.layout.layout(true);
						owner.layout.resize();
						if (owner.visible) owner.update();
					}
				} catch (_) { }
			}
		};
	}
	// Выбранные элементы отображаются набором statictext. Это надёжнее
	// listbox в динамически пересобираемом ScriptUI и не создаёт скрытого scroll.
	function addStaticMultiSelect(parent, options) {
		options = options || {};
		var preferredWidth = options.preferredWidth || self.contentWidth(),
			items = options.items instanceof Array ? options.items : [],
			normalize = options.normalize || function (value) { return value instanceof Array ? cloneObj(value) : []; },
			values = normalize(options.storedValue),
			host = addSelectorHost(parent, preferredWidth, options.label, options.help, options.selectHelp, items.length),
			group = host.group,
			add = host.add,
			selectedHost = host.selectedHost;
		function displayLabel(value) {
			for (var i = 0; i < items.length; i++) {
				var data = itemData(items[i]);
				if (String(data.value) == String(value)) return data.label;
			}
			return String(value === undefined || value === null ? "" : value);
		}
		function openSelector() {
			if (!items.length) return;
			var sel = self.showItemSelector({
				title: options.selectorTitle || options.label || "",
				searchHelp: options.searchHelp || "",
				items: items,
				selectedValues: values,
				multiselect: true
			});
			if (sel === null) return;
			values = normalize(sel);
			rebuildSelected();
		}
		function attachClick(control, itemLabel) {
			control.helpTip = (itemLabel ? itemLabel + "\n" : "") + (options.selectHelp || "");
			if (!items.length || control._img2imgSelectorAttached) return;
			control._img2imgSelectorAttached = true;
			try { control.addEventListener("mousedown", openSelector); }
			catch (_) { try { control.onClick = openSelector; } catch (_) { } }
		}
		function rebuildSelected() {
			var rows = [], i, control, itemLabel;
			if (!values.length) rows.push({ text: "› " + (options.emptyText || ""), label: "" });
			else {
				for (i = 0; i < values.length; i++) {
					itemLabel = displayLabel(values[i]);
					rows.push({ text: "› " + itemLabel, label: itemLabel });
				}
			}
			try { selectedHost.visible = false; } catch (_) { }
			while (selectedHost.children.length < rows.length)
				selectedHost.add("statictext");
			while (selectedHost.children.length > rows.length)
				selectedHost.remove(selectedHost.children[selectedHost.children.length - 1]);
			for (i = 0; i < rows.length; i++) {
				control = selectedHost.children[i];
				try { control.visible = false; } catch (_) { }
				control.text = rows[i].text;
				attachClick(control, rows[i].label);
				try { control.visible = true; } catch (_) { }
			}
			try { selectedHost.visible = true; } catch (_) { }
			host.relayout();
		}
		add.onClick = openSelector;
		rebuildSelected();
		return {
			getValue: function () { return cloneObj(values); },
			control: add,
			container: group
		};
	}
	function addModuleMultiSelect(parent, schema, storedValue, preferredWidth) {
		return addStaticMultiSelect(parent, {
			preferredWidth: preferredWidth,
			label: label(schema),
			help: help(schema),
			items: schema.items || [],
			storedValue: storedValue,
			normalize: function (value) { return self.normalizeMultiselect(schema, value); },
			selectorTitle: str.selectModules,
			searchHelp: str.modulesSearch,
			selectHelp: str.selectModules,
			emptyText: str.modulesNone
		});
	}
	this.addForgeLoraMultiSelect = function (parent, items, storedValue, preferredWidth) {
		var normalizedItems = normalizeForgeLoraList(items),
			values = normalizeForgeLoraSelection(normalizedItems, storedValue),
			controlWidth = preferredWidth || self.contentWidth(),
			host = addSelectorHost(parent, controlWidth, str.lora, str.selectLora, str.selectLora, normalizedItems.length),
			group = host.group,
			add = host.add,
			selectedHost = host.selectedHost;
		function selectionNames() {
			var names = [];
			for (var i = 0; i < values.length; i++) {
				var parsed = parseForgeLoraEntry(values[i]);
				if (parsed.name && !arrayContainsCaseInsensitive(names, parsed.name)) names.push(parsed.name);
			}
			return names;
		}
		function weightMap() {
			var map = {};
			for (var i = 0; i < values.length; i++) {
				var parsed = parseForgeLoraEntry(values[i]);
				if (parsed.name) map[String(parsed.name).toUpperCase()] = parsed.weight;
			}
			return map;
		}
		function openSelector() {
			if (!normalizedItems.length) return;
			var sel = self.showItemSelector({
				title: str.selectLora,
				searchHelp: str.loraSearch,
				items: normalizedItems,
				selectedValues: selectionNames(),
				multiselect: true
			});
			if (sel === null) return;
			var weights = weightMap();
			values = [];
			for (var i = 0; i < sel.length; i++) {
				var name = String(sel[i] || "").replace(/^\s+|\s+$/g, ""),
					key = String(name).toUpperCase(),
					weight = weights.hasOwnProperty(key) ? weights[key] : 1;
				if (!name) continue;
				values.push(formatForgeLoraEntry(name, weight));
			}
			values = normalizeForgeLoraSelection(normalizedItems, values);
			rebuildSelected();
		}
		function setLabelTooltip(control, itemLabel) {
			control.helpTip = itemLabel ? itemLabel : str.lora;
		}
		function rebuildSelected() {
			try { selectedHost.visible = false; } catch (_) { }
			while (selectedHost.children.length)
				selectedHost.remove(selectedHost.children[selectedHost.children.length - 1]);
			if (!values.length) {
				var empty = selectedHost.add("statictext");
				empty.text = "› " + str.lorasNone;
				setLabelTooltip(empty, "");
				if (normalizedItems.length) {
					try { empty.addEventListener("mousedown", openSelector); }
					catch (_) { try { empty.onClick = openSelector; } catch (_) { } }
				}
			} else {
				for (var i = 0; i < values.length; i++) {
					(function (index) {
						var parsed = parseForgeLoraEntry(values[index]),
							sliderControl = self.addSlider(selectedHost, "lora:" + parsed.name, 0, 1, parsed.weight, {
								controlWidth: controlWidth,
								displayValue: formatForgeLoraWeight(parsed.weight)
							});
						setLabelTooltip(sliderControl.title, parsed.name);
						sliderControl.valueText.helpTip = parsed.name;
						var weightStepper = createSliderStepper(sliderControl.slider, 0.01, 0);
						function syncWeight(finalize) {
							var next = finalize ? weightStepper.finish() : weightStepper.sync(false);
							next = roundTo(next, 2);
							sliderControl.valueText.text = formatForgeLoraWeight(next);
							values[index] = formatForgeLoraEntry(parsed.name, next);
							if (finalize) values = normalizeForgeLoraSelection(normalizedItems, values);
						}
						sliderControl.slider.onChanging = function () { syncWeight(false); };
						sliderControl.slider.onChange = function () { syncWeight(true); };
					})(i);
				}
			}
			try { selectedHost.visible = true; } catch (_) { }
			host.relayout();
		}
		add.onClick = openSelector;
		rebuildSelected();
		return {
			getValue: function () { return cloneObj(values); },
			control: add,
			container: group
		};
	};
	function addMultiSelect(parent, schema, storedValue, preferredWidth) {
		var group = self.addColumn(parent, 0, "top"),
			title = group.add("statictext"),
			list = group.add("listbox", undefined, [], { multiselect: true });
		self.setFixedWidth(group, preferredWidth);
		title.text = label(schema);
		title.helpTip = help(schema);
		list.preferredSize = [preferredWidth, self.multiSelectHeight];
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
	this.addDynamic = function (parent, schema, storedValue, preferredWidth, options) {
		if (schema.id == "positive_prompt" || schema.id == "negative_prompt") return addPromptControl(parent, schema, storedValue, options);
		if (schema.type == "dropdown") {
			var dropdownControl = self.addDropdown(parent, label(schema), schema.items || [], preferredWidth || self.contentWidth());
			dropdownControl.title.helpTip = help(schema);
			var selectedItem = self.selectDropdown(dropdownControl.dropdown, storedValue);
			dropdownControl.dropdown.enabled = !!selectedItem && dropdownControl.dropdown.items.length > 0;
			// Catalog items may carry an optional model-specific helpTip. Keep it on
			// the dropdown itself so hovering the selected model shows its settings.
			function updateSelectedItemHelp() {
				var item = dropdownControl.dropdown.selection;
				dropdownControl.dropdown.helpTip = item && item.itemHelp ? String(item.itemHelp) : "";
			}
			dropdownControl.dropdown.onChange = updateSelectedItemHelp;
			updateSelectedItemHelp();
			return {
				getValue: function () { return read(dropdownControl.dropdown, false); },
				control: dropdownControl.dropdown,
				container: dropdownControl.group
			};
		}
		if (isModuleMultiSelect(schema)) return addModuleMultiSelect(parent, schema, storedValue, preferredWidth || self.contentWidth());
		if (schema.type == "multiselect") return addMultiSelect(parent, schema, storedValue, preferredWidth || self.contentWidth());
		if (schema.type == "checkbox") {
			var checkbox = parent.add("checkbox");
			checkbox.text = label(schema);
			checkbox.helpTip = help(schema);
			checkbox.value = toBooleanValue(storedValue);
			return { getValue: function () { return checkbox.value; }, control: checkbox, container: checkbox };
		}
		if (schema.type == "integer" || schema.type == "float") return addNumericControl(parent, schema, storedValue, options);
		var group = self.addColumn(parent, 0, "top"), title = group.add("statictext");
		title.text = label(schema);
		title.helpTip = help(schema);
		var properties = schema.type == "multiline" ? { multiline: true, scrolling: true } : {},
			edit = group.add("edittext", undefined, String(storedValue === undefined ? "" : storedValue), properties);
		edit.preferredSize = [preferredWidth || self.contentWidth(), schema.type == "multiline" ? 70 : -1];
		return { getValue: function () { return edit.text; }, control: edit, container: group };
	};
	this.addToolbarRow = function (parent, totalWidth, buttonCount) {
		buttonCount = Math.max(0, parseInt(buttonCount, 10) || 0);
		totalWidth = Math.max(self.presetButtonWidth * buttonCount + 100, Number(totalWidth) || self.contentWidth());
		var buttonBlockWidth = self.presetButtonWidth * buttonCount,
			dropdownWidth = Math.max(100, totalWidth - buttonBlockWidth),
			row = parent.add("group{orientation:'row',alignChildren:['left','center'],spacing:0,margins:0}"),
			dropdown = row.add("dropdownlist"),
			buttons = row.add("group{orientation:'row',alignChildren:['left','center'],spacing:0,margins:0}"),
			controls = [];
		self.setFixedWidth(row, totalWidth);
		dropdown.alignment = ["left", "center"];
		self.setFixedWidth(dropdown, dropdownWidth);
		buttons.alignment = ["right", "center"];
		self.setFixedWidth(buttons, buttonBlockWidth);
		for (var i = 0; i < buttonCount; i++) {
			var button = buttons.add("button");
			self.setFixedWidth(button, self.presetButtonWidth);
			controls.push(button);
		}
		return { row: row, dropdown: dropdown, buttons: buttons, controls: controls };
	};
	this.addPresetToolbar = function (parent, totalWidth, refreshHelp) {
		var toolbar = self.addToolbarRow(parent, totalWidth, 4),
			controls = toolbar.controls,
			refresh = controls[0],
			add = controls[1],
			save = controls[2],
			remove = controls[3],
			symbols = [str.presetRefreshButton, str.presetAddButton, str.presetSaveButton, str.presetDeleteButton],
			tips = [refreshHelp || str.presetRestore, str.presetAdd, str.presetSave, str.presetDelete];
		for (var i = 0; i < controls.length; i++) {
			controls[i].text = symbols[i];
			controls[i].helpTip = tips[i];
		}
		return { row: toolbar.row, dropdown: toolbar.dropdown, refresh: refresh, add: add, save: save, remove: remove };
	};
	function parseNumericText(value) {
		var text = String(value === undefined || value === null ? "" : value)
			.replace(/^\s+|\s+$/g, "").replace(/,/g, ".");
		if (!text) return NaN;
		var number = Number(text);
		return isFinite(number) ? number : NaN;
	}
	function isSafeIntegerText(value) {
		var text = String(value || "").replace(/^[-+]/, "").replace(/^0+/, "") || "0",
			limit = "9007199254740991";
		if (!/^\d+$/.test(text)) return false;
		return text.length < limit.length || (text.length == limit.length && text <= limit);
	}
	function canRoundNumericValue(value, step, origin) {
		return isFinite(value) && isFinite(step) && isFinite(origin) &&
			Math.abs(value) <= 9007199254740991 &&
			Math.abs(origin) <= 9007199254740991 &&
			Math.abs(value - origin) <= 9007199254740991;
	}
	function addNumericControl(parent, schema, storedValue, options) {
		// Тип из /object_info имеет приоритет над типом JSON-литерала.
		// Например, KSampler.denoise является FLOAT, но значение 1 в API JSON
		// сериализуется как integer. Дополнительная проверка защищает интерфейс
		// от противоречивых числовых метаданных.
		var integer = isIntegerNumericSchema(schema),
			value = parseNumericText(storedValue);
		if (isNaN(value)) value = parseNumericText(schema.value) || 0;
		if (generation.isSeedControl(schema)) {
			var seedGroup = self.addColumn(parent, 0);
			self.setFixedWidth(seedGroup, self.contentWidth());
			var seedTitle = seedGroup.add("statictext"),
				seedRow = seedGroup.add("group{orientation:'row',alignChildren:['fill','center'],spacing:0,margins:0}");
			self.setFixedWidth(seedRow, self.contentWidth());
			var seedEdit = seedRow.add("edittext"),
				seedRefresh = seedRow.add("button{preferredSize:[30,-1]}");
			seedTitle.text = self.label(schema);
			seedTitle.helpTip = self.help(schema);
			seedEdit.alignment = ["fill", "center"];
			seedEdit.preferredSize.width = Math.max(100, self.contentWidth() - 30);
			seedEdit.text = String(storedValue === undefined ? value : storedValue);
			seedEdit.onChanging = function () { filterNumericEditText(this); };
			seedRefresh.text = "↻";
			seedRefresh.helpTip = str.randomSeed;
			seedRefresh.onClick = function () { seedEdit.text = String(generation.makeRandomSeed(schema)); };
			return { getValue: function () { return seedEdit.text; }, control: seedEdit, container: seedGroup };
		}
		var hasMinimum = hasNumericSchemaValue(schema.min),
			hasMaximum = hasNumericSchemaValue(schema.max),
			preferredSlider = isPreferredSliderControl(schema),
			min = hasMinimum ? parseNumericText(schema.min) : Math.min(0, value),
			max = hasMaximum ? parseNumericText(schema.max) : Math.max(100, value * 2, 1),
			stepsControl = isStepsControl(schema);
		if ((!hasMinimum || !hasMaximum) && !preferredSlider) {
			return addNumericEditControl(parent, schema, storedValue, integer);
		}
		if (isNaN(min)) min = 0;
		if (isNaN(max) || max <= min) {
			if (!preferredSlider) return addNumericEditControl(parent, schema, storedValue, integer);
			max = min + 100;
		}
		if (stepsControl) {
			max = Math.min(max, 100);
			if (max <= min) min = Math.min(min, 99);
		}
		var explicitStep = hasExplicitNumericStep(schema),
			rawStep = explicitStep ? parseNumericText(schema.step) : null,
			step = numericControlStep(schema, integer),
			precision = integer ? 0 : numberPrecision(step),
			// Для Comfy большие диапазоны с единичным либо неуказанным шагом
			// удобнее вводить точно с клавиатуры, чем выбирать длинным слайдером.
			useLargeRangeEdit = options && options.backend == BACKEND_COMFY &&
				hasMinimum && hasMaximum && max > 2048 &&
				(!explicitStep || rawStep == 1);
		if (useLargeRangeEdit || Math.abs(max - min) > 10000000) {
			return addNumericEditControl(parent, schema, storedValue, integer);
		}
		value = clamp(value, min, max);
		var scale = Math.pow(10, precision),
			sliderMinimum = Math.round(min * scale),
			sliderMaximum = Math.round(max * scale),
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
		var sliderStepper = createSliderStepper(sliderControl.slider, sliderStep, sliderMinimum);
		function syncSliderValue(finalize) {
			var sliderPosition = finalize ? sliderStepper.finish() : sliderStepper.sync(false),
				cur;
			sliderPosition = clamp(Math.round(sliderPosition), sliderMinimum, sliderMaximum);
			sliderControl.slider.value = sliderPosition;
			cur = roundTo(sliderPosition / scale, precision);
			sliderControl.valueText.text = formatNumber(cur, integer, precision);
			return cur;
		}
		sliderControl.slider.onChange = function () { syncSliderValue(true); };
		sliderControl.slider.onChanging = function () { syncSliderValue(false); };
		return {
			getValue: function () {
				var cur = syncSliderValue(false);
				return integer ? Math.round(cur) : cur;
			},
			control: sliderControl.slider, container: sliderControl.group
		};
	}
	function addNumericEditControl(parent, schema, storedValue, integer) {
		var editGroup = self.addColumn(parent, 0);
		self.setFixedWidth(editGroup, self.contentWidth());
		var title = editGroup.add("statictext"),
			edit = editGroup.add("edittext{preferredSize:[" + self.contentWidth() + ",-1]}");
		title.text = self.label(schema);
		title.helpTip = self.help(schema);
		edit.text = String(storedValue === undefined ? schema.value : storedValue);
		// Во время набора фильтруются только недопустимые символы. Полная
		// проверка диапазона выполняется после завершения ввода и ещё раз при
		// чтении значения перед сохранением/генерацией.
		edit.onChanging = function () { filterNumericEditText(this); };
		edit.onChange = function () { readValue(true); };
		function readValue(updateText) {
			var text = normalizeNumericEditText(edit.text),
				fallback = parseNumericText(schema.value),
				value, rawInteger = integer && /^[+-]?\d+$/.test(text) ? text : "";
			// Generic PrimitiveInt may expose a 64-bit range. Keep integers above
			// JavaScript's exact range as text and let Python validate them.
			if (rawInteger && !isSafeIntegerText(rawInteger)) {
				if (updateText) edit.text = rawInteger;
				return rawInteger;
			}
			if (text == "" || text == "-" || text == "." || text == "-.") {
				value = isNaN(fallback) ? 0 : fallback;
			} else {
				value = parseNumericText(text);
				if (isNaN(value)) value = isNaN(fallback) ? 0 : fallback;
			}
			var hasMinimum = hasNumericSchemaValue(schema.min),
				hasMaximum = hasNumericSchemaValue(schema.max),
				min = hasMinimum ? parseNumericText(schema.min) : null,
				max = hasMaximum ? parseNumericText(schema.max) : null,
				explicitStep = hasExplicitNumericStep(schema),
				step = explicitStep || integer ? numericControlStep(schema, integer) : null,
				origin = hasMinimum && !isNaN(min) ? min : 0;
			if (hasMinimum && !isNaN(min)) value = Math.max(min, value);
			if (hasMaximum && !isNaN(max)) value = Math.min(max, value);
			// step=1 for an integer needs no rounding. Avoid any step calculation
			// when a huge min/max would exceed exact IEEE-754 integer arithmetic:
			// this was able to turn a small PrimitiveInt value such as 20 into 0.
			if (step !== null && (!integer || step > 1) && canRoundNumericValue(value, step, origin))
				value = roundByStep(value, step, origin);
			if (hasMinimum && !isNaN(min)) value = Math.max(min, value);
			if (hasMaximum && !isNaN(max)) value = Math.min(max, value);
			if (integer) {
				value = Math.round(value);
				if (updateText) edit.text = String(value);
				return String(value);
			}
			if (step !== null) {
				var precision = numberPrecision(step);
				value = roundTo(value, precision);
				if (updateText) edit.text = formatNumber(value, false, precision);
			} else if (updateText) {
				edit.text = String(value);
			}
			return value;
		}
		return {
			getValue: function () { return readValue(true); },
			control: edit, container: editGroup
		};
	}
	function filterNumericEditText(edit) {
		var normalized = normalizeNumericEditText(edit.text);
		if (edit.text != normalized) edit.text = normalized;
	}
	function normalizeNumericEditText(value) {
		var text = String(value === undefined || value === null ? "" : value)
				.replace(/^\s+|\s+$/g, "").replace(/,/g, "."),
			negative = text.charAt(0) == "-";
		// Keep one decimal separator even for integer fields while the user is
		// typing. On commit integer values are rounded; this avoids turning
		// locale input such as 20,0 into the unrelated integer 200.
		text = text.replace(/-/g, "").replace(/[^0-9.]/g, "");
		var dot = text.indexOf(".");
		if (dot >= 0) text = text.substring(0, dot + 1) + text.substring(dot + 1).replace(/\./g, "");
		return (negative ? "-" : "") + text;
	}
	function isDenoiseNumericSchema(schema) {
		var id = String(schema && schema.id || ""),
			input = String(schema && schema.input || "").toLowerCase();
		return startsWithSemantic(id, "denoise") ||
			input == "denoise" || input == "denoise_strength" || input == "strength";
	}
	function isIntegerNumericSchema(schema) {
		var declared = String(schema && schema.type || "").toLowerCase();
		if (declared == "float" || declared == "number") return false;
		if (declared != "integer" && declared != "int") return false;
		// Fractional metadata is incompatible with an integer slider.
		var keys = ["value", "min", "max", "step"];
		for (var i = 0; i < keys.length; i++) {
			var raw = schema ? schema[keys[i]] : null,
				number = parseNumericText(raw);
			if (raw !== undefined && raw !== null && String(raw) != "" &&
				!isNaN(number) && Math.abs(number - Math.round(number)) > 0.000000001)
				return false;
		}
		// Denoise in a normalized 0..1 range must remain fractional.
		if (isDenoiseNumericSchema(schema) &&
			hasNumericSchemaValue(schema.min) && hasNumericSchemaValue(schema.max) &&
			parseNumericText(schema.max) - parseNumericText(schema.min) <= 1.000000001)
			return false;
		return true;
	}
	function hasNumericSchemaValue(value) {
		return value !== undefined && value !== null && String(value) != "" && !isNaN(parseNumericText(value));
	}
	function hasExplicitNumericStep(schema) {
		return schema && schema.step !== undefined && schema.step !== null &&
			String(schema.step) != "" && !isNaN(parseNumericText(schema.step));
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
		var step = schema.step !== undefined ? parseNumericText(schema.step) : (integer ? 1 : 0.01);
		if (isNaN(step) || step <= 0) step = integer ? 1 : 0.01;
		if (!integer && isCoarseHalfStepControl(schema)) step = 0.5;
		// ScriptUI использует целочисленную шкалу после масштабирования.
		// У FLOAT denoise с диапазоном 0..1 шаг, равный всему диапазону,
		// оставляет только две позиции. Такой результат считается ошибочным
		// UI-описанием и заменяется сотой частью диапазона.
		if (!integer && isDenoiseNumericSchema(schema) &&
			hasNumericSchemaValue(schema.min) && hasNumericSchemaValue(schema.max)) {
			var min = parseNumericText(schema.min),
				max = parseNumericText(schema.max),
				span = max - min;
			if (span > 0 && span <= 1.000000001 && step >= span)
				step = span / 100;
		}
		// В интерфейсе поддерживается до шести знаков после запятой.
		if (!integer && step < 0.000001) step = 0.000001;
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
	function addPromptControl(parent, schema, storedValue, options) {
		var group = parent.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:0}"),
			title = group.add("statictext"),
			toolbar = self.addPresetToolbar(group, self.contentWidth(), str.presetRestore),
			presetList = toolbar.dropdown,
			refresh = toolbar.refresh,
			add = toolbar.add,
			save = toolbar.save,
			remove = toolbar.remove,
			edit = group.add("edittext", undefined, "", { multiline: true, scrollable: true }),
			translate = group.add("button");
		self.setFixedWidth(group, self.contentWidth());
		edit.preferredSize = [self.contentWidth(), self.promptHeight()];
		self.setFixedWidth(translate, self.contentWidth());
		var context = schema.id == "negative_prompt" ? "negative" : "positive",
			presetStore = cfg.getPromptPresetStore(context),
			profile = options && options.profile ? options.profile : null,
			storedPresetName = profile && profile.selectedPromptPresets
				? String(profile.selectedPromptPresets[context] || "")
				: "";
		if (storedPresetName && !presetStore.hasOwnProperty(storedPresetName)) storedPresetName = "";
		title.text = self.label(schema);
		title.helpTip = self.help(schema);
		translate.text = str.translate + " → EN";
		translate.helpTip = str.translatePromptHelp;
		edit.text = String(storedValue === undefined ? "" : storedValue);
		fillPresets(storedPresetName);
		rememberSelectedPreset();
		updateControlState();
		presetList.onChange = function () {
			rememberSelectedPreset();
			edit.text = presets.applyPrompt(selectedPresetText());
			updateControlState();
		};
		refresh.onClick = function () {
			edit.text = presets.applyPrompt(selectedPresetText());
			updateControlState();
		};
		add.onClick = function () {
			var currentName = presetList.selection ? presetList.selection.text : str.presetDefault,
				name = messages.prompt(str.presetNamePrompt, currentName + str.presetCopy, str.presetNew);
			name = name == null ? "" : String(name).replace(/^\s+|\s+$/g, "");
			if (!name) return;
			if (String(name).toLowerCase() == String(str.presetDefault).toLowerCase()) {
				messages.error(str.errDefaultPreset);
				return;
			}
			if (presetStore.hasOwnProperty(name) && messages.confirm(String(str.errPreset).replace("%1", name), str.presetNew) !== true) return;
			presetStore[name] = presets.promptText(edit.text);
			fillPresets(name);
			rememberSelectedPreset();
			updateControlState();
		};
		save.onClick = function () {
			if (!presetList.selection || presetList.selection.index == 0) return;
			presetStore[presetList.selection.text] = presets.promptText(edit.text);
			updateControlState();
		};
		remove.onClick = function () {
			if (!presetList.selection || presetList.selection.index == 0) return;
			var index = presetList.selection.index,
				name = presetList.selection.text;
			if (messages.confirm(str.presetDeleteConfirmA + name + str.presetDeleteConfirmB, APP.name) !== true) return;
			delete presetStore[name];
			fillPresets(null, Math.max(0, index - 1));
			rememberSelectedPreset();
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
					messages.error(str.errTranslate);
				}
			} catch (e) {
				messages.error((e && e.message ? e.message : str.errTranslate));
			}
		};
		return { getValue: function () { return edit.text; }, control: edit, container: group };
		function selectedPresetText() {
			return presetList.selection && presetList.selection.index > 0
				? String(presetStore[presetList.selection.text] || "")
				: "";
		}
		function rememberSelectedPreset() {
			if (!profile) return;
			if (!isObjectMap(profile.selectedPromptPresets))
				profile.selectedPromptPresets = { positive: "", negative: "" };
			profile.selectedPromptPresets[context] = presetList.selection && presetList.selection.index > 0
				? String(presetList.selection.text)
				: "";
		}
		function updateControlState() {
			var cur = presets.promptText(edit.text),
				stored = selectedPresetText(),
				changed = cur != stored,
				customPreset = !!(presetList.selection && presetList.selection.index > 0);
			translate.enabled = edit.text.length > 0;
			remove.enabled = customPreset;
			save.enabled = customPreset && changed;
			refresh.enabled = changed;
			add.enabled = cur.length > 0;
		}
		function fillPresets(selectName, selectIndex) {
			presetList.removeAll();
			presetList.add("item", str.presetDefault);
			var names = [], key, i, sel = 0;
			for (key in presetStore) if (presetStore.hasOwnProperty(key)) names.push(key);
			names.sort(function (a, b) {
				a = String(a).toLowerCase(); b = String(b).toLowerCase();
				return a == b ? 0 : (a > b ? 1 : -1);
			});
			for (i = 0; i < names.length; i++) {
				presetList.add("item", names[i]);
				if (names[i] == selectName) sel = i + 1;
			}
			if (selectName == null && selectIndex != null) sel = Math.min(Math.max(0, selectIndex), presetList.items.length - 1);
			presetList.selection = sel;
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
		self.setFixedWidth(group, self.contentWidth());
		if (options.title) {
			var title = group.add("statictext");
			title.text = options.title;
		}
		var dropdown = group.add("dropdownlist{preferredSize:[" + self.contentWidth() + ",-1]}");
		dropdown.minimumSize.width = dropdown.maximumSize.width = self.contentWidth();
		dropdown.helpTip = options.helpTip || "";
		function currentPath() {
			var path = options.getValue() || "",
				file = path ? new File(path) : null;
			if (path && (!file.exists || !isSupportedReferenceImage(file.fsName))) {
				options.setValue("");
				path = "";
			}
			return path;
		}
		function rebuild(selectedPath) {
			dropdown.removeAll();
			var noneItem = dropdown.add("item", str.noneReference);
			noneItem.filePath = "";
			var history = cfg.cleanReferenceHistory().slice(0),
				selectedIndex = 0,
				selectedFound = false;
			for (var i = 0; i < history.length; i++) {
				var item = dropdown.add("item", shortenReferencePath(history[i]));
				item.filePath = history[i];
				if (selectedPath && String(history[i]).toUpperCase() == String(selectedPath).toUpperCase()) {
					selectedIndex = i + 1;
					selectedFound = true;
				}
			}
			if (selectedPath && !selectedFound && (new File(selectedPath)).exists) {
				var currentItem = dropdown.add("item", shortenReferencePath(selectedPath));
				currentItem.filePath = selectedPath;
				selectedIndex = dropdown.items.length - 1;
			}
			var browseItem = dropdown.add("item", str.browse);
			browseItem.browse = true;
			dropdown.selection = Math.min(selectedIndex, dropdown.items.length - 1);
		}
		rebuild(currentPath());
		dropdown.onChange = function () {
			if (!this.selection) return;
			if (this.selection.browse) {
				var file = (new File(" ")).openDlg(str.selectReferenceImage, REFERENCE_IMAGE_FILTER);
				if (!file) {
					rebuild(currentPath());
					return;
				}
				if (!isSupportedReferenceImage(file.fsName)) {
					messages.error(str.errReferenceImageFormat);
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
				? str.imageReference + " " + (index + 1) + " — " + (binding.label || binding.id)
				: str.imageReference,
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
		var enabled = profile.values.hasOwnProperty("image_stitch")
				? toBooleanValue(profile.values.image_stitch)
				: toBooleanValue(schema.image_stitch_default),
			inputLimit = imageStitchInputLimit(schema),
			checkbox = parent.add("checkbox");
		checkbox.text = str.imageStitchInputs;
		checkbox.value = enabled;
		controls.image_stitch = { getValue: function () { return checkbox.value; }, control: checkbox };
		if (enabled) for (var index = 0; index < inputLimit; index++) addForgeImageInput(parent, profile, index);
		checkbox.onClick = function () {
			profile.values.image_stitch = this.value;
			if (onVisibilityChanged) onVisibilityChanged(this.value);
		};
	}
	function addForgeImageInput(parent, profile, index) {
		return addHistoryFileDropdown(parent, {
			helpTip: str.imageStitchInput + " " + (index + 1),
			getValue: function () { return profile.imageStitchInputs[index] || ""; },
			setValue: function (value) { profile.imageStitchInputs[index] = value || ""; }
		});
	}
	function shortenReferencePath(path) {
		var separator = path.indexOf("\\") >= 0 ? "\\" : "/",
			parts = String(path).split(separator);
		if (parts.length <= 2) return path;
		var res = [parts[0]], cur = parts[0].length, tail = parts[parts.length - 1];
		for (var i = 1; i < parts.length - 1; i++) {
			if (cur + parts[i].length + tail.length < 36) { res.push(parts[i]); cur += parts[i].length; }
			else { res.push("..."); break; }
		}
		res.push(tail);
		return res.join(separator);
	}
	function addResizeControl(parent, bounds, profile, schema) {
		if (profile.autoResize === undefined) profile.autoResize = cfg.autoResize;
		profile.autoResize = toBooleanValue(profile.autoResize);
		if (profile.manualScale === undefined) profile.manualScale = 1;
		if (!profile.resizePreset) profile.resizePreset = presets.normalizeResizeName(profile.resizePreset, cfg.resizePresets);
		var group = parent.add("group{orientation:'column',alignChildren:['fill','top'],spacing:0,margins:0}");
		self.setFixedWidth(group, self.contentWidth());
		var titleRow = group.add("group{orientation:'row',alignChildren:['left','center'],spacing:0,margins:0}");
		self.setFixedWidth(titleRow, self.contentWidth());
		var checkbox = titleRow.add("checkbox"),
			resizeTitleWidth = self.contentWidth() - self.autoResizeCheckboxWidth - self.sliderValueWidth,
			title = titleRow.add("statictext"),
			valueText = titleRow.add("statictext{justify:'right'}");
		self.setFixedWidth(checkbox, self.autoResizeCheckboxWidth);
		self.setFixedWidth(title, resizeTitleWidth);
		self.setFixedWidth(valueText, self.sliderValueWidth);
		var slider = group.add("slider{minvalue:1,maxvalue:400}");
		self.setFixedWidth(slider, self.contentWidth());
		var resizeStepper = createSliderStepper(slider, 1, 1);
		try { self.enableHoverFocus(slider); } catch (_) { }
		var presetGroup = group.add("group{orientation:'column',alignChildren:['fill','center'],spacing:0,margins:[0,5,0,0]}");
		self.setFixedWidth(presetGroup, self.contentWidth());
		var presetDropdown = presetGroup.add("dropdownlist{preferredSize:[" + self.contentWidth() + ",-1]}");
		presetDropdown.minimumSize.width = presetDropdown.maximumSize.width = self.contentWidth();
		checkbox.value = toBooleanValue(profile.autoResize);
		checkbox.helpTip = str.autoResize;
		presetDropdown.helpTip = str.resizePreset;
		title.helpTip = schema.has_size_binding ? str.sizeWorkflowBinding : str.sizeFromInput;
		fillPresetList();
		setSliderValue();
		function syncResizeValue(finalize) {
			var pointerActive = resizeStepper.pointerActive,
				sliderValue = finalize ? resizeStepper.finish() : resizeStepper.sync(false);
			// При drag оставляем "магнит" около 100%, но клавиши/колесо должны
			// менять значение строго на 0.01 за одно движение.
			if (pointerActive && sliderValue >= 97 && sliderValue <= 103) {
				sliderValue = 100;
				resizeStepper.reset(100);
			}
			var scale = Math.max(0.01, Math.round(sliderValue) / 100);
			// Ручное изменение масштаба означает явный переход из Auto в Manual.
			// Значение становится постоянным manualScale и сохраняется в профиль/Action
			// до тех пор, пока пользователь снова явно не включит Автомасштаб.
			if (checkbox.value) {
				checkbox.value = false;
				profile.autoResize = false;
				presetGroup.enabled = false;
			}
			profile.manualScale = scale;
			valueText.text = scale.toFixed(2);
			title.text = setTitle();
		}
		slider.onChange = function () { syncResizeValue(true); };
		slider.onChanging = function () { syncResizeValue(false); };
		checkbox.onClick = function () {
			profile.autoResize = this.value;
			presetGroup.enabled = this.value;
			setSliderValue();
		};
		presetDropdown.onChange = function () {
			if (!this.selection) return;
			profile.resizePreset = cfg.resizePresets[this.selection.index].name;
			setSliderValue();
		};
		presetGroup.enabled = checkbox.value;
		function fillPresetList() {
			presetDropdown.removeAll();
			for (var i = 0; i < cfg.resizePresets.length; i++) presetDropdown.add("item", presets.formatResize(cfg.resizePresets[i]));
			var preset = presets.findResize(profile.resizePreset, cfg.resizePresets),
				sel = presets.findResizeIndex(preset.name, cfg.resizePresets);
			presetDropdown.selection = sel < 0 ? 0 : sel;
			profile.resizePreset = preset.name;
		}
		function setTitle() {
			var scale = profile.autoResize
					? autoScale(bounds, presets.findResize(profile.resizePreset, cfg.resizePresets))
					: profile.manualScale,
				size = calculateSizeFromScale(bounds.width, bounds.height, scale, resolveProfileSizeMultiple(schema, profile));
			var text = profile.autoResize ? str.autoResize : str.resize,
				mp = Math.floor(size.width * size.height / 10000) / 100;
			return scale != 1
				? text + ": " + size.width + "x" + size.height + " (" + mp + " MP)"
				: text;
		}
		function setSliderValue() {
			if (profile.autoResize) {
				var scale = autoScale(bounds, presets.findResize(profile.resizePreset, cfg.resizePresets));
				resizeStepper.reset(scale * 100);
				valueText.text = scale.toFixed(2);
				title.text = setTitle();
			} else {
				resizeStepper.reset(profile.manualScale * 100);
				profile.manualScale = Math.round(slider.value) / 100;
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
			var res = fn(progress);
			progress.complete();
			return res;
		} finally {
			progress.close();
		}
	}
	function StartupProgress(msg, timeout, delay) {
		var w = null, text = null, bar = null,
			currentMessage = msg,
			baseValue = 2,
			started = (new Date()).getTime(),
			stageStarted = started,
			totalTimeout = Math.max(1000, timeout || START_TIMEOUT),
			shown = false;
		delay = Math.max(0, Number(delay) || 0);
		function createWindow() {
			if (w) return;
			w = new Window("palette", APP.name);
			w.orientation = "column";
			w.alignChildren = ["fill", "top"];
			w.spacing = 5;
			w.margins = 15;
			text = w.add("statictext");
			text.preferredSize = [420, -1];
			bar = w.add("progressbar", undefined, 0, 100);
			bar.preferredSize = [420, 15];
			text.text = currentMessage;
			bar.value = baseValue;
		}
		function ensureShown(force) {
			if (shown) return true;
			if (!force && (new Date()).getTime() - started < delay) return false;
			createWindow();
			if (delay) stageStarted = (new Date()).getTime();
			w.center(); w.show(); w.update();
			shown = true;
			return true;
		}
		this.show = function () { ensureShown(true); };
		this.setStage = function (newMessage, value) {
			currentMessage = newMessage || currentMessage;
			var currentValue = shown ? bar.value : baseValue;
			baseValue = Math.max(currentValue, Math.min(96, value === undefined ? currentValue : value));
			if (shown || !delay) stageStarted = (new Date()).getTime();
			if (shown) { bar.value = baseValue; text.text = currentMessage; w.update(); }
		};
		this.pulse = function () {
			if (!ensureShown(false)) return;
			var elapsed = (new Date()).getTime() - stageStarted,
				addition = Math.min(12, elapsed / totalTimeout * 70);
			bar.value = Math.min(97, baseValue + addition);
			text.text = currentMessage + "  " + roundTo(elapsed / 1000, 1) + " " + str.secondsShort;
			w.update();
		};
		this.complete = function () {
			if (shown) { bar.value = 100; text.text = str.progressReady; w.update(); }
		};
		this.close = function () { if (w) try { w.close(); } catch (_) { } };
	}
	this.addImageReferenceControls = addImageReferenceControls;
	this.addForgeImageStitchControls = addForgeImageStitchControls;
	this.addResizeControl = addResizeControl;
	this.runWithPaletteProgress = runWithPaletteProgress;
	this.createStartupProgress = function (msg, timeout) { return new StartupProgress(msg, timeout, 0); };
	this.createDelayedStartupProgress = function (msg, timeout, delay) { return new StartupProgress(msg, timeout, delay); };
}
// ---
// ДВУХЭТАПНЫЙ PROGRESS ГЕНЕРАЦИИ
// Первый сегмент ждёт подготовки/начала sampling, второй — завершения backend.
// Разделение позволяет корректно показывать долгую загрузку модели до sampling.
// ---
function GenerationProgress() {
	var payload = null,
		res = null,
		firstAnswer = null,
		prepareTitle = "",
		generateTitle = "",
		delayKey = "",
		delayMax = GENERATION_RUN_DEFAULT_EXPECTED_MS,
		requestId = null;
	this.begin = function (options) {
		options = options || {};
		payload = options.command || null;
		res = null;
		firstAnswer = null;
		prepareTitle = options.titles && options.titles.prepare ? options.titles.prepare : "";
		generateTitle = options.titles && options.titles.generate ? options.titles.generate : "";
		delayKey = options.timingKey || "";
		delayMax = options.timingMax || GENERATION_RUN_DEFAULT_EXPECTED_MS;
		requestId = options.requestId || (payload ? payload.request_id : null);
	};
	this.run = function () {
		try {
			if (!app.doProgressSegmentTask(
				GENERATION_PREPARE_SEGMENT, 0, GENERATION_TOTAL_SEGMENTS,
				"generationStageOne()"
			)) return cancelProgress();
			if (!firstAnswer || firstAnswer.type == "error" || firstAnswer.message != "init") {
				res = firstAnswer;
				return true;
			}
			if (!app.doProgressSegmentTask(
				GENERATION_RUN_SEGMENT, GENERATION_PREPARE_SEGMENT,
				GENERATION_TOTAL_SEGMENTS,
				"generationStageTwo()"
			)) return cancelProgress();
			return true;
		} catch (progressError) {
			if (!isUserCancellation(progressError)) throw progressError;
			return cancelProgress();
		}
	};
	function cancelProgress() {
		res = { type: "cancelled", message: "" };
		api.interrupt(requestId);
		return false;
	}
	this.stageOne = function () {
		// Comfy остаётся на первом сегменте до реального начала sampler, а
		// загрузка крупной модели может занимать больше прежних двух минут.
		var prepareTimeout = payload && payload.type == "forge_generate"
				? 5 * 60 * 1000
				: cfg.generationTimeout * 1000,
			answer = api.startGeneration({
				command: payload,
				timeout: prepareTimeout,
				title: prepareTitle || str.progressPrepare,
				max: GENERATION_PREPARE_EXPECTED_MS,
				progressCurve: "hyperbolic"
			});
		if (answer === false) return false;
		firstAnswer = answer;
		return true;
	};
	this.stageTwo = function () {
		var answer = api.finishGeneration({
			timeout: cfg.generationTimeout * 1000,
			title: generateTitle || str.progressGenerate,
			max: delayMax,
			delayKey: delayKey,
			requestId: requestId
		});
		res = answer === false ? false : answer;
		return answer !== false;
	};
	this.getResult = function () { return res; };
	this.clear = function () {
		payload = null;
		res = null;
		firstAnswer = null;
		prepareTitle = "";
		generateTitle = "";
		delayKey = "";
		delayMax = GENERATION_RUN_DEFAULT_EXPECTED_MS;
		requestId = null;
	};
}
function prepareSelectionLayer(selection) { return generation.prepareSelectionLayer(selection); }
function checkSelection(res) { return generation.checkSelection(res); }
function placeResultHistory() { return generation.placeResultHistory(); }
function runGenerationProgress() { return generationProgress.run(); }
function generationStageOne() { return generationProgress.stageOne(); }
function generationStageTwo() { return generationProgress.stageTwo(); }
// Уменьшает выделение до ближайшей меньшей кратности, никогда не расширяя
// исходную область. sourceBounds хранит первоначальные границы, поэтому функция
// безопасно вызывается повторно после смены workflow/preset.
function fitSelectionBounds(res, multiple) {
	multiple = clamp(parseInt(multiple, 10) || 1, 1, 256);
	if (!res.sourceBounds) res.sourceBounds = cloneObj(res.bounds);
	var source = res.sourceBounds,
		b = res.bounds,
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
	if (b.right - b.left < multiple || b.bottom - b.top < multiple)
		throw new Error(str.errSelectionTooSmall + " " + multiple + " px.");
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
// ---
// XMP-МЕТАДАННЫЕ СЛОЯ
// JSON хранится в собственном namespace.
// ---
function LayerMetadata() {
	var cur = null;
	function ensureLibrary() {
		try {
			if (ExternalObject.AdobeXMPScript == undefined)
				ExternalObject.AdobeXMPScript = new ExternalObject("lib:AdobeXMPScript");
			XMPMeta.registerNamespace(APP.xmp.namespace, APP.xmp.prefix);
			return true;
		} catch (_) { return false; }
	}
	this.set = function (value) { cur = cloneObj(value); };
	this.write = function () {
		if (!ensureLibrary() || !cur) return false;
		try {
			var xmp;
			try { xmp = new XMPMeta(app.activeDocument.activeLayer.xmpMetadata.rawData); }
			catch (_) { xmp = new XMPMeta(); }
			xmp.setProperty(APP.xmp.namespace, APP.xmp.property, jsonStringify(cur));
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
			catch (_) { value = null; }
			if (!isObjectMap(value)) return null;
			if (value.backend == BACKEND_COMFY && !value.workflow_id && !value.relative_path) return null;
			if (value.backend == BACKEND_FORGE && !value.workspace_id) return null;
			return value;
		} catch (_) { }
		return null;
	};
}
// ---
// СЕТЕВОЙ МОСТ JSX <-> ЛОКАЛЬНЫЙ PYTHON API
// Команды отправляются на один порт, ответы приходят на другой. Для генерации
// используется ACK "init", после которого JSX продолжает ждать итоговый ответ.
// ---
function BridgeApi() {
	// В сетевом слое явная локализация оставлена намеренно: здесь нужны гарантированно обычные строки.
	var self = this;
	this.isRunning = function () { return checkConnection(API_HOST, API_PORT_SEND); };
	this.initialize = function (progress, knownRunning) {
		// init() уже делает TCP-check, чтобы решить, показывать ли progress. Не
		// повторяем его на обычном тёплом запуске и не ищем Python-файл, пока
		// действительно не понадобится запуск нового процесса.
		var deadline = (new Date()).getTime() + START_TIMEOUT,
			running = knownRunning === undefined ? self.isRunning() : !!knownRunning,
			runningInfo = null;
		if (running) {
			try { runningInfo = self.ping(progress); }
			catch (pingError) {
				// Редкая гонка: процесс мог завершиться между TCP-check и ping.
				// Дополнительный check выполняется только после ошибки.
				if (self.isRunning()) throw pingError;
				running = false;
			}
			if (running) {
				validatePythonProtocol(runningInfo);
				progress = waitForPythonReady(runningInfo, progress, deadline);
				return true;
			}
		}
		var pythonFile = findPythonModule();
		if (!pythonFile) throw new Error(str.errPythonMissingA + API_FILE + str.errPythonMissingB);
		if (progress) progress.setStage(str.progressStartPython, 3);
		clearStartupStatus();
		var launchStartedAt = (new Date()).getTime();
		if (pythonFile.execute() === false) throw new Error(str.errPythonExecute + "\n" + pythonFile.fsName);
		var startupState = waitForConnection(Math.max(1, deadline - (new Date()).getTime()), progress, launchStartedAt);
		if (startupState !== true) {
			var logPath = startupState && startupState.log_file ? String(startupState.log_file) : startupLogPath();
			if (startupState && startupState.status == "installing")
				throw new Error(str.errPythonInstallTimeout +
					(startupState.message ? "\n\n" + startupState.message : "") +
					(logPath ? "\n\n" + str.pythonLog + logPath : ""));
			throw new Error(str.errPythonStartA + API_HOST + ":" + API_PORT_SEND + str.errPythonStartB +
				(logPath ? "\n\n" + str.pythonLog + logPath : ""));
		}
		var started = self.ping(progress);
		validatePythonProtocol(started);
		waitForPythonReady(started, progress, deadline);
		return true;
	};
	this.ping = function (progress, timeout) { return call("ping", null, timeout || SHORT_TIMEOUT, progress); };
	this.translate = function (text, progress) { return call("translate", { text: text || "" }, TRANSLATE_TIMEOUT, progress); };
	this.handshake = function (progress, settings, backendStatusMode, verifyBackend, backendProbeToken) {
		var source = settings || cfg;
		return call("handshake", {
			host: source.backendHost,
			comfyPort: source.comfyPort,
			forgePort: source.forgePort,
			workflowsFolder: source.workflowsFolder,
			generationTimeout: source.generationTimeout,
			pythonIdleTimeout: source.pythonIdleTimeout,
			backendMonitorInterval: source.backendMonitorInterval,
			backendStatusMode: backendStatusMode || "cached",
			verifyBackend: verifyBackend || "",
			backendProbeToken: backendProbeToken || ""
		}, SHORT_TIMEOUT, progress);
	};
	this.probeBackends = function (settings, progress) {
		var source = settings || cfg;
		return call("probe_backends", {
			host: source.backendHost,
			comfyPort: source.comfyPort,
			forgePort: source.forgePort
		}, SHORT_TIMEOUT, progress);
	};
	this.workflowList = function (progress) { return call("workflow_list", null, ANALYZE_TIMEOUT, progress); };
	this.forgeSchemaList = function (progress) {
		return call("forge_schema_list", { schema_folder: cfg.forgeSchemasFolder || "" }, ANALYZE_TIMEOUT, progress);
	};
	this.forgeSchemaGet = function (schemaId, progress) {
		return call("forge_schema_get", {
			schema_id: schemaId,
			schema_folder: cfg.forgeSchemasFolder || ""
		}, ANALYZE_TIMEOUT, progress);
	};
	this.forgeCatalog = function (sources, force, progress) {
		return call("forge_catalog", {
			sources: sources instanceof Array ? sources : [],
			force: !!force,
			schema_folder: cfg.forgeSchemasFolder || ""
		}, 5 * 60 * 1000, progress);
	};
	this.workflowGet = function (workflowId, overrides, relativePath, progress) {
		return call("workflow_get", workflowMessage(workflowId, overrides, relativePath), ANALYZE_TIMEOUT, progress);
	};
	this.workflowReinitialize = function (workflowId, overrides, relativePath, progress) {
		return call("workflow_reinitialize", workflowMessage(workflowId, overrides, relativePath), ANALYZE_TIMEOUT, progress);
	};
	this.workflowSaveValues = function (workflowId, relativePath, overrides, values, destinationPath, progress) {
		var msg = workflowMessage(workflowId, overrides, relativePath);
		msg.values = values || {};
		msg.destination_path = destinationPath || "";
		return call("workflow_save_values", msg, ANALYZE_TIMEOUT, progress);
	};
	this.forgeSchemaSaveValues = function (schemaId, values, destinationPath, selectedLoras, progress) {
		return call("forge_schema_save_values", {
			schema_id: schemaId,
			schema_folder: cfg.forgeSchemasFolder || "",
			values: values || {},
			destination_path: destinationPath || "",
			selected_loras: selectedLoras instanceof Array ? cloneObj(selectedLoras) : []
		}, ANALYZE_TIMEOUT, progress);
	};
	this.interrupt = function (requestId) {
		try { fire(makeCommand("interrupt", { request_id: requestId || "" }, requestId)); } catch (_) { }
	};
	this.startGeneration = function (options) {
		options = options || {};
		return requestWithOptions(options.command, {
			timeout: options.timeout,
			title: options.title,
			max: options.max,
			progressCurve: options.progressCurve,
			trackDelay: true,
			delayKey: options.delayKey,
			interruptOnTimeout: true
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
			requestId: options.requestId,
			interruptOnTimeout: true
		});
	};
	function call(type, msg, timeout, progress) {
		return unwrapAnswer(request(makeCommand(type, msg), timeout, progress));
	}
	function workflowMessage(workflowId, overrides, relativePath) {
		return {
			workflow_id: workflowId,
			relative_path: relativePath || "",
			binding_overrides: cleanBindingOverrides(overrides)
		};
	}
	function request(command, timeout, progress) {
		return requestWithOptions(command, { timeout: timeout, progress: progress });
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
		return requestWithOptions(makeCommand("ack", {}, options.requestId), options);
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
			progressCurve = options.progressCurve || "exponential",
			trackDelay = !!options.trackDelay,
			delayKey = options.delayKey,
			expectedRequestId = options.expectedRequestId,
			interruptOnTimeout = !!options.interruptOnTimeout,
			t1 = (new Date()).getTime(),
			t2 = t1,
			t3 = t1,
			slice = 0;
		if (title) {
			max = Number(max) || timeout || 7500;
			if (max < 1) max = 1;
		}
		for (; ;) {
			t2 = (new Date()).getTime();
			if (t2 - t1 > timeout) {
				if (interruptOnTimeout && expectedRequestId) {
					try { self.interrupt(expectedRequestId); } catch (_) { }
				}
				listener.close();
				throw new Error(str.errApiTimeout);
			}
			if (t2 - t3 >= API_POLL_INTERVAL) {
				if (progress) progress.pulse();
				if (title) {
					// taskLength — доля оставшейся части текущего segment. Для
					// подготовки гипербола t/(t+max) медленно и без скачков стремится
					// к границе 20%. Генерация сохраняет адаптивную экспоненту, которая
					// за ожидаемое время проходит PROGRESS_STAGE_TARGET сегмента.
					var progressDelta = t2 - t3;
					if (progressDelta > 0 && progressCurve == "hyperbolic")
						slice = progressDelta / (max + t2 - t1);
					else slice = progressDelta > 0
						? 1 - Math.pow(1 - PROGRESS_STAGE_TARGET, progressDelta / max)
						: 0;
					var text = trackDelay
						? title + "\t " + Math.floor((t2 - t1) / 100) / 10 + " s. "
						: title;
					if (!app.doProgressTask(slice, "workChunk('" + escapeProgressText(text) + "');")) {
						// GenerationProgress.cancelProgress() отправит единственный interrupt.
						listener.close();
						return false;
					}
				}
				t3 = t2;
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
				if (expectedRequestId && String(answer.request_id || "") != String(expectedRequestId)) continue;
				listener.close();
				if (trackDelay && delayKey) {
					try { generationTimings.saveDelay(delayKey, t2 - t1); } catch (_) { }
				}
				return answer;
			}
			$.sleep(API_POLL_SLEEP);
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
	function waitForConnection(timeout, startup, launchStartedAt) {
		var started = (new Date()).getTime(), lastStatus = null, lastStage = "";
		while ((new Date()).getTime() - started < timeout) {
			if (checkConnection(API_HOST, API_PORT_SEND)) {
				clearStartupStatus();
				return true;
			}
			var status = consumeStartupStatus(launchStartedAt);
			if (status) {
				lastStatus = status;
				if (status.status == "error")
					throw new Error(str.errPythonStartupDetails +
						(status.message ? "\n\n" + status.message : "") +
						(status.log_file ? "\n\n" + str.pythonLog + status.log_file : ""));
				var stage = status.status == "installing"
					? str.progressInstallPython + (status.message ? " " + status.message : "")
					: str.progressStartPython;
				if (startup && stage != lastStage) { startup.setStage(stage, 3); lastStage = stage; }
			}
			if (startup) startup.pulse();
			$.sleep(25);
		}
		clearStartupStatus();
		return lastStatus || false;
	}
	function validatePythonProtocol(info) {
		if (String(info && info.protocol) != String(API_PROTOCOL))
			throw new Error(localize(str.errApiProtocolA) + (info ? info.protocol : "") +
				localize(str.errApiProtocolB) + API_PROTOCOL + ".");
	}
	function ensureStartupProgress(progress) {
		if (progress) return progress;
		startupProgress = ui.createStartupProgress(str.progressStartPython, START_TIMEOUT + ANALYZE_TIMEOUT);
		startupProgress.show();
		return startupProgress;
	}
	function waitForPythonReady(info, progress, deadline) {
		var lastStage = "";
		for (; ;) {
			validatePythonProtocol(info);
			clearStartupStatus();
			var status = String(info.startup_status || "ready").toLowerCase(),
				message = String(info.startup_message || ""),
				logPath = String(info.startup_log_file || startupLogPath() || "");
			if (status == "ready") return progress;
			if (status == "error")
				throw new Error(str.errPythonStartupDetails +
					(message ? "\n\n" + message : "") +
					(logPath ? "\n\n" + str.pythonLog + logPath : ""));
			progress = ensureStartupProgress(progress);
			var stage = status == "installing"
				? str.progressInstallPython + (message ? " " + message : "")
				: str.progressStartPython;
			if (stage != lastStage) { progress.setStage(stage, 3); lastStage = stage; }
			if ((new Date()).getTime() >= deadline) {
				if (status == "installing")
					throw new Error(str.errPythonInstallTimeout +
						(message ? "\n\n" + message : "") +
						(logPath ? "\n\n" + str.pythonLog + logPath : ""));
				throw new Error(str.errPythonStartA + API_HOST + ":" + API_PORT_SEND + str.errPythonStartB +
					(logPath ? "\n\n" + str.pythonLog + logPath : ""));
			}
			progress.pulse();
			$.sleep(500);
			info = self.ping(progress, Math.min(SHORT_TIMEOUT, Math.max(1, deadline - (new Date()).getTime())));
		}
	}
	function startupStatusFile() {
		var root = "";
		try { root = String($.getenv("LOCALAPPDATA") || ""); } catch (_) { }
		return root ? new File(root + "/" + APP.tempFolder + "/" + APP.startupFile) : null;
	}
	function startupLogPath() {
		var statusFile = startupStatusFile();
		return statusFile ? statusFile.parent.parent.fsName + "/" + API_FILE + ".log" : "";
	}
	function readUtf8File(file) {
		if (!file || !file.exists) return null;
		var opened = false;
		try {
			file.encoding = "UTF-8";
			if (!file.open("r")) return null;
			opened = true;
			var value = file.read();
			file.close(); opened = false;
			return value;
		} catch (_) { return null; }
		finally { if (opened) try { file.close(); } catch (_) { } }
	}
	function clearStartupStatus() {
		var file = startupStatusFile();
		discardStartupStatusFile(file);
	}
	function discardStartupStatusFile(file) {
		if (!file || !file.exists) return;
		try { if (file.remove()) return; } catch (_) { }
		// Если Windows временно не разрешил удалить файл, очищаем содержимое,
		// чтобы прочитанный status не мог повлиять на следующий запуск.
		var opened = false;
		try {
			file.encoding = "UTF-8";
			if (!file.open("w")) return;
			opened = true;
			file.write("");
			file.close(); opened = false;
		} catch (_) { }
		finally { if (opened) try { file.close(); } catch (_) { } }
	}
	function consumeStartupStatus(launchStartedAt) {
		var file = startupStatusFile(), source = readUtf8File(file);
		if (source === null) return null;
		var status = null;
		try { status = jsonParse(source); } catch (_) { status = null; }
		// Не удаляем более новую запись, если Python успел атомарно заменить
		// status-файл между первым чтением и подтверждением содержимого.
		var current = readUtf8File(file);
		if (current === source) discardStartupStatusFile(file);
		if (!status || typeof status != "object") return null;
		var startedAt = Number(status.started_at || 0) * 1000;
		if (launchStartedAt && startedAt && startedAt < launchStartedAt - 5000) return null;
		status.status = String(status.status || "").toLowerCase();
		return status;
	}
	function checkConnection(host, port) {
		var socket = new Socket();
		try { return socket.open(host + ":" + port, "UTF-8"); }
		catch (_) { return false; }
		finally { try { socket.close(); } catch (_) { } }
	}
	// Пустые/default overrides обычно не отправляются. Явно настроенные пустые
	// Reference/Empty-image списки являются осознанным состоянием и сохраняются.
	function cleanBindingOverrides(value) {
		if (!value) return {};
		var res = {},
			sizeMode = value.sizeMode == "source_image" || value.sizeMode == "binding"
				? value.sizeMode
				: "auto";
		if (value.input) res.input = value.input;
		if (value.mask) res.mask = value.mask;
		if (value.references instanceof Array && (value.references.length || value.referencesConfigured === true))
			res.references = value.references.slice(0);
		if (value.referencesConfigured === true) res.referencesConfigured = true;
		if (value.emptyInputs instanceof Array) res.emptyInputs = value.emptyInputs.slice(0);
		if (value.output) res.output = value.output;
		if (sizeMode != "auto") res.sizeMode = sizeMode;
		if (sizeMode == "binding" && value.size) res.size = value.size;
		return res;
	}
	function makeCommand(type, msg, requestId) {
		return { protocol: API_PROTOCOL, request_id: requestId || createRequestId(), type: type, message: msg || {} };
	}
	function unwrapAnswer(response) {
		if (!response) throw new Error(str.errEmptyApiAnswer);
		if (response.type == "error") throw new Error(apiErrorText(response));
		return response.message;
	}
}
// ---
// СЕРИАЛИЗАЦИЯ ОБЪЕКТОВ В ActionDescriptor
// Используется и для DESC, и для playbackParameters. Null/function пропускаются;
// вложенные объекты и массивы рекурсивно превращаются в Descriptor/List.
// ---
function DescriptorCodec() {
	function readDescriptor(target, desc) {
		for (var i = 0; i < desc.count; i++) {
			var key = desc.getKey(i),
				name = t2s(key),
				type = desc.getType(key);
			if (type == DescValueType.BOOLEANTYPE) target[name] = desc.getBoolean(key);
			else if (type == DescValueType.STRINGTYPE) target[name] = desc.getString(key);
			else if (type == DescValueType.INTEGERTYPE) target[name] = desc.getInteger(key);
			else if (type == DescValueType.LARGEINTEGERTYPE) target[name] = desc.getLargeInteger(key);
			else if (type == DescValueType.DOUBLETYPE) target[name] = desc.getDouble(key);
			else if (type == DescValueType.OBJECTTYPE) {
				target[name] = {};
				readDescriptor(target[name], desc.getObjectValue(key));
			} else if (type == DescValueType.LISTTYPE) target[name] = readList(desc.getList(key));
		}
		return target;
	}
	function readList(list) {
		var res = [];
		for (var i = 0; i < list.count; i++) {
			var type = list.getType(i);
			if (type == DescValueType.BOOLEANTYPE) res.push(list.getBoolean(i));
			else if (type == DescValueType.STRINGTYPE) res.push(list.getString(i));
			else if (type == DescValueType.INTEGERTYPE) res.push(list.getInteger(i));
			else if (type == DescValueType.LARGEINTEGERTYPE) res.push(list.getLargeInteger(i));
			else if (type == DescValueType.DOUBLETYPE) res.push(list.getDouble(i));
			else if (type == DescValueType.OBJECTTYPE) res.push(readDescriptor({}, list.getObjectValue(i)));
			else if (type == DescValueType.LISTTYPE) res.push(readList(list.getList(i)));
		}
		return res;
	}
	function writeDescriptor(object, integerNumbers) {
		var desc = new ActionDescriptor();
		for (var name in object) if (object.hasOwnProperty(name)) {
			var value = object[name];
			if (value === null || value === undefined || typeof value == "function") continue;
			var key;
			try { key = s2t(String(name)); } catch (_) { continue; }
			if (typeof value == "boolean") desc.putBoolean(key, value);
			else if (typeof value == "string") desc.putString(key, value);
			else if (typeof value == "number") {
				if (integerNumbers && value == Math.round(value) && value >= -2147483648 && value <= 2147483647)
					desc.putInteger(key, value);
				else desc.putDouble(key, value);
			} else if (value instanceof Array) desc.putList(key, writeList(value, integerNumbers));
			else if (typeof value == "object") desc.putObject(key, s2t("object"), writeDescriptor(value, integerNumbers));
		}
		return desc;
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
	this.readInto = function (target, desc) { return readDescriptor(target || {}, desc); };
	this.toDescriptor = function (object, integerNumbers) { return writeDescriptor(object || {}, !!integerNumbers); };
}
// ---
// КОНФИГУРАЦИЯ, ПРОФИЛИ И ХРАНИЛИЩА
// self.data — сериализуемый объект; bindProperties создаёт удобные ссылки
// self.foo. Перед записью syncData возвращает изменённые ссылки обратно в data.
// ---
function Config() {
	var self = this,
		loadWarnings = [],
		recoveredFromBackup = false,
		keys = [
			"backendHost", "activeBackend", "comfyPort", "forgePort", "workflowsFolder", "forgeSchemasFolder", "selectedWorkflow", "selectedForgePreset",
			"autoResize", "sizeMultiple", "resizePresets",
			"flatten", "rasterizeImage", "keepAspectRatioDuringPlace", "recordSettingsToAction", "writeLayerMetadata",
			"selectBrush", "brushOpacity", "generationTimeout", "pythonIdleTimeout", "backendMonitorInterval", "workflowProfiles", "forgeProfiles",
			"workflowCatalog", "forgeCatalog", "referenceHistory", "promptPresets", "descSaveCount"
		];
	this.data = defaultData();
	this.bindProperties = function () {
		for (var i = 0; i < keys.length; i++) this[keys[i]] = this.data[keys[i]];
	};
	function syncData() {
		for (var i = 0; i < keys.length; i++) self.data[keys[i]] = self[keys[i]];
	}
	function profileStore(name) {
		var store = self[name];
		if (!isObjectMap(store)) store = {};
		self[name] = self.data[name] = store;
		return store;
	}
	function normalizeBaseProfile(profile) {
		if (!isObjectMap(profile.values)) profile.values = {};
		if (!isObjectMap(profile.selectedPromptPresets))
			profile.selectedPromptPresets = { positive: "", negative: "" };
		if (typeof profile.selectedPromptPresets.positive != "string") profile.selectedPromptPresets.positive = "";
		if (typeof profile.selectedPromptPresets.negative != "string") profile.selectedPromptPresets.negative = "";
		if (profile.visibleControls !== null && profile.visibleControls !== undefined && !(profile.visibleControls instanceof Array))
			profile.visibleControls = null;
		if (!profile.resizePreset) profile.resizePreset = presets.normalizeResizeName("", self.resizePresets);
		if (profile.manualScale === undefined) profile.manualScale = 1;
		return profile;
	}
	function normalizeProfileStore(store) {
		if (!isObjectMap(store)) return;
		for (var profileId in store)
			if (store.hasOwnProperty(profileId) && isObjectMap(store[profileId]))
				normalizeBaseProfile(store[profileId]);
	}
	function applyLoadedData(loaded) {
		self.data = loaded ? mergeDefaults(defaultData(), loaded) : defaultData();
		self.bindProperties();
		if (!self.resizePresets || !self.resizePresets.length)
			self.resizePresets = self.data.resizePresets = presets.defaultResize();
		normalizeProfileStore(self.workflowProfiles);
		normalizeProfileStore(self.forgeProfiles);
		self.cleanReferenceHistory();
	}
	this.cleanReferenceHistory = function () {
		var source = self.referenceHistory instanceof Array ? self.referenceHistory : [], cleaned = [];
		for (var i = 0; i < source.length && cleaned.length < 10; i++) {
			var file = new File(source[i]);
			if (file.exists && isSupportedReferenceImage(file.fsName) &&
				!arrayContainsCaseInsensitive(cleaned, file.fsName)) cleaned.push(file.fsName);
		}
		self.referenceHistory = self.data.referenceHistory = cleaned;
		return cleaned;
	};
	this.rememberReference = function (path) {
		var file = new File(path || "");
		if (!file.exists || !isSupportedReferenceImage(file.fsName)) return;
		var cur = self.cleanReferenceHistory(),
			res = [file.fsName];
		for (var i = 0; i < cur.length && res.length < 10; i++)
			if (!arrayContainsCaseInsensitive(res, cur[i])) res.push(cur[i]);
		self.referenceHistory = self.data.referenceHistory = res;
	};
	// Раз в несколько сотен сохранений DESC удаляем только профили, чьи
	// исходные JSON уже отсутствуют. Проверка выполняется отдельно для каждого
	// реально доступного backend; существующий, но повреждённый JSON не удаляет профиль.
	function probeCleanupBackends() {
		var res = { comfy: false, forge: false };
		try {
			if (!api || !api.isRunning()) return res;
			var status = api.probeBackends(self);
			res.comfy = !!(status && status.backends && status.backends.comfy && status.backends.comfy.available);
			res.forge = !!(status && status.backends && status.backends.forge && status.backends.forge.available);
		} catch (_) { }
		return res;
	}
	function deletedProfileCleanupPlan(data, available) {
		var plan = { workflowIds: [], forgeIds: [], workflowCatalog: [], forgeCatalog: [] };
		available = available || {};
		function existingFile(root, relativePath) {
			if (!relativePath) return true;
			try { return (new File(root.fsName + "/" + String(relativePath))).exists; }
			catch (_) { return true; }
		}
		var workflowFolder = String(data.workflowsFolder || "");
		if (workflowFolder && available.comfy) {
			var workflowRoot = new Folder(workflowFolder);
			if (workflowRoot.exists) {
				var workflowProfiles = isObjectMap(data.workflowProfiles) ? data.workflowProfiles : {};
				for (var workflowId in workflowProfiles) if (workflowProfiles.hasOwnProperty(workflowId)) {
					var profile = workflowProfiles[workflowId], relativePath = profile && profile.relativePath;
					if (relativePath && !existingFile(workflowRoot, relativePath)) plan.workflowIds.push(workflowId);
				}
				if (data.workflowCatalog instanceof Array)
					for (var wi = 0; wi < data.workflowCatalog.length; wi++) {
						var workflow = data.workflowCatalog[wi], path = workflow && workflow.relative_path;
						if (path && !existingFile(workflowRoot, path)) plan.workflowCatalog.push(wi);
					}
			}
		}
		var forgeFolder = String(data.forgeSchemasFolder || "");
		if (forgeFolder && available.forge) {
			var forgeRoot = new Folder(forgeFolder);
			if (forgeRoot.exists) {
				var existing = {}, files = [];
				try { files = forgeRoot.getFiles(); } catch (_) { files = []; }
				for (var fi = 0; fi < files.length; fi++) {
					if (!(files[fi] instanceof File) || !/\.json$/i.test(files[fi].name)) continue;
					existing[String(files[fi].name).replace(/\.json$/i, "").toLowerCase()] = true;
				}
				var forgeProfiles = isObjectMap(data.forgeProfiles) ? data.forgeProfiles : {};
				for (var presetId in forgeProfiles) if (forgeProfiles.hasOwnProperty(presetId) && !existing[String(presetId).toLowerCase()])
					plan.forgeIds.push(presetId);
				if (data.forgeCatalog instanceof Array)
					for (var fj = 0; fj < data.forgeCatalog.length; fj++) {
						var item = data.forgeCatalog[fj], fileName = item && item.file;
						if (fileName && !existing[String(fileName).replace(/\.json$/i, "").toLowerCase()]) plan.forgeCatalog.push(fj);
					}
			}
		}
		return plan;
	}
	function applyDeletedProfileCleanup(data, plan) {
		var i, profiles;
		profiles = isObjectMap(data.workflowProfiles) ? data.workflowProfiles : {};
		for (i = 0; i < plan.workflowIds.length; i++) delete profiles[plan.workflowIds[i]];
		profiles = isObjectMap(data.forgeProfiles) ? data.forgeProfiles : {};
		for (i = 0; i < plan.forgeIds.length; i++) delete profiles[plan.forgeIds[i]];
		if (data.workflowCatalog instanceof Array)
			for (i = plan.workflowCatalog.length - 1; i >= 0; i--) data.workflowCatalog.splice(plan.workflowCatalog[i], 1);
		if (data.forgeCatalog instanceof Array)
			for (i = plan.forgeCatalog.length - 1; i >= 0; i--) data.forgeCatalog.splice(plan.forgeCatalog[i], 1);
	}
	function cleanupSaveData(data, plan) {
		var res = {}, key;
		for (key in data) if (data.hasOwnProperty(key)) res[key] = data[key];
		if (plan.workflowIds.length) res.workflowProfiles = cloneObj(data.workflowProfiles);
		if (plan.forgeIds.length) res.forgeProfiles = cloneObj(data.forgeProfiles);
		if (plan.workflowCatalog.length) res.workflowCatalog = cloneObj(data.workflowCatalog);
		if (plan.forgeCatalog.length) res.forgeCatalog = cloneObj(data.forgeCatalog);
		applyDeletedProfileCleanup(res, plan);
		return res;
	}
	// Формирует облегчённый снимок Action. Если запись настроек выключена,
	// сохраняется только маркер и флаг, а при playback читается актуальный DESC.
	function actionData() {
		var res = {
			actionDataVersion: 1,
			recordSettingsToAction: !!self.recordSettingsToAction
		};
		if (!self.recordSettingsToAction) return res;
		res = cloneObj(self.data);
		res.actionDataVersion = 1;
		// Эти данные являются общими для DESC и всех Actions либо всегда
		// восстанавливаются из актуального backend. Не записываем их в шаг Action.
		delete res.promptPresets;
		delete res.referenceHistory;
		delete res.workflowCatalog;
		delete res.forgeCatalog;
		delete res.descSaveCount;
		delete res.pythonIdleTimeout;
		delete res.backendMonitorInterval;
		var profiles = isObjectMap(res.workflowProfiles) ? res.workflowProfiles : {};
		for (var workflowId in profiles) if (profiles.hasOwnProperty(workflowId) && profiles[workflowId]) {
			delete profiles[workflowId].schemaCache;
			delete profiles[workflowId].schemaCacheStamp;
			delete profiles[workflowId].schemaCacheVersion;
			delete profiles[workflowId].schemaCacheUsed;
		}
		return res;
	}
	function settingsFile(suffix) {
		return new File(app.preferencesFolder + "/" + APP.settingsFile + (suffix || ""));
	}
	function fileError(file) {
		try { return file && file.error ? String(file.error) : ""; }
		catch (_) { return ""; }
	}
	function operationError(prefix, file) {
		var detail = fileError(file);
		return String(prefix) + "\n" + file.fsName + (detail ? "\n" + detail : "");
	}
	function readSettingsData(file) {
		var opened = false;
		try {
			file.encoding = "BINARY";
			if (!file.open("r")) throw new Error(operationError(str.errSettingsReadFile, file));
			opened = true;
			var stream = file.read();
			if (fileError(file)) throw new Error(operationError(str.errSettingsReadFile, file));
			if (file.close() === false) throw new Error(operationError(str.errSettingsReadFile, file));
			opened = false;
			var desc = new ActionDescriptor(), loaded = {};
			desc.fromStream(stream);
			descriptorCodec.readInto(loaded, desc);
			return loaded;
		} finally {
			if (opened) try { file.close(); } catch (_) { }
		}
	}
	function writeSettingsStream(file, stream) {
		var opened = false;
		if (file.exists && !file.remove()) throw new Error(operationError(str.errSettingsWriteFile, file));
		try {
			file.encoding = "BINARY";
			if (!file.open("w")) throw new Error(operationError(str.errSettingsWriteFile, file));
			opened = true;
			var written = file.write(stream);
			if (written === false || fileError(file)) throw new Error(operationError(str.errSettingsWriteFile, file));
			if (file.close() === false) throw new Error(operationError(str.errSettingsWriteFile, file));
			opened = false;
		} finally {
			if (opened) try { file.close(); } catch (_) { }
		}
		// Проверяем не только факт записи, но и возможность восстановить
		// ActionDescriptor до замены рабочего файла.
		readSettingsData(new File(file.fsName));
	}
	function restoreBackup(primaryPath, backupPath) {
		var primary = new File(primaryPath), backup = new File(backupPath);
		if (primary.exists && !primary.remove()) return false;
		return backup.exists && backup.rename(APP.settingsFile);
	}
	this.consumeLoadWarnings = function () {
		var res = loadWarnings;
		loadWarnings = [];
		return res;
	};
	this.load = function () {
		loadWarnings = [];
		recoveredFromBackup = false;
		var file = settingsFile(""), backup = settingsFile(".bak"), loaded = null, primaryError = null;
		if (file.exists) {
			try { loaded = readSettingsData(file); }
			catch (e) { primaryError = e; }
		}
		if (!loaded && backup.exists) {
			try {
				loaded = readSettingsData(backup);
				recoveredFromBackup = true;
				loadWarnings.push({
					key: "settings-backup-recovered",
					message: str.settingsBackupRecovered + "\n" + backup.fsName +
						(primaryError ? "\n\n" + str.settingsPrimaryReadError + "\n" + errorMessageText(primaryError) : "")
				});
			} catch (backupError) {
				if (primaryError) throw new Error(
					str.errSettingsUnreadable + "\n\n" + errorMessageText(primaryError) +
					"\n\n" + errorMessageText(backupError)
				);
				throw backupError;
			}
		}
		if (!loaded && primaryError) throw new Error(str.errSettingsUnreadable + "\n\n" + errorMessageText(primaryError));
		applyLoadedData(loaded);
	};
	// Action загружается поверх defaults, а не поверх текущего DESC. После
	// этого init() отдельно возвращает общие prompt/reference библиотеки из DESC.
	this.loadFromAction = function () {
		var loaded = {};
		try { descriptorCodec.readInto(loaded, app.playbackParameters); }
		catch (_) { loaded = {}; }
		delete loaded.actionDataVersion;
		applyLoadedData(loaded);
	};
	this.saveToAction = function () {
		syncData();
		playbackParameters = descriptorCodec.toDescriptor(actionData());
	};
	this.save = function () {
		syncData();
		var previousSaveCount = parseInt(self.descSaveCount, 10);
		if (isNaN(previousSaveCount) || previousSaveCount < 0 || previousSaveCount >= DESC_PROFILE_CLEANUP_INTERVAL) previousSaveCount = 0;
		var nextSaveCount = previousSaveCount + 1,
			cleanupDue = nextSaveCount >= DESC_PROFILE_CLEANUP_INTERVAL,
			cleanupBackends = cleanupDue ? probeCleanupBackends() : null,
			cleanupPlan = cleanupDue ? deletedProfileCleanupPlan(self.data, cleanupBackends) : null,
			saveData = cleanupDue ? cleanupSaveData(self.data, cleanupPlan) : self.data,
			file = settingsFile(""),
			temp = settingsFile(".tmp"),
			backup = settingsFile(".bak"),
			primaryPath = file.fsName,
			tempPath = temp.fsName,
			backupPath = backup.fsName,
			hadPrimary = file.exists,
			primaryMoved = false,
			promoted = false;
		if (cleanupDue) saveData.descSaveCount = 0;
		else self.descSaveCount = self.data.descSaveCount = nextSaveCount;
		try {
			var desc = descriptorCodec.toDescriptor(saveData),
				stream = desc.toStream();
			writeSettingsStream(temp, stream);
			if (hadPrimary) {
				if (recoveredFromBackup && backup.exists) {
					// Основной файл уже признан повреждённым, а .bak успешно
					// прочитан. Не заменяем исправную резервную копию заведомо
					// плохим файлом; удаляем его только после проверки temp.
					if (!(new File(primaryPath)).remove())
						throw new Error(operationError(str.errSettingsReplaceFile, new File(primaryPath)));
					primaryMoved = true;
				} else {
					if (backup.exists && !backup.remove()) throw new Error(operationError(str.errSettingsWriteFile, backup));
					if (!(new File(primaryPath)).rename(APP.settingsFile + ".bak"))
						throw new Error(operationError(str.errSettingsBackupFile, new File(primaryPath)));
					primaryMoved = true;
				}
			}
			if (!(new File(tempPath)).rename(APP.settingsFile)) {
				if (primaryMoved && !restoreBackup(primaryPath, backupPath))
					throw new Error(str.errSettingsRestoreBackup + "\n" + backupPath);
				throw new Error(operationError(str.errSettingsReplaceFile, new File(tempPath)));
			}
			promoted = true;
			// Финальная проверка защищает от успешного rename повреждённого
			// временного файла и от редких ошибок файловой системы.
			readSettingsData(new File(primaryPath));
			recoveredFromBackup = false;
			if (cleanupDue) {
				applyDeletedProfileCleanup(self.data, cleanupPlan);
				self.descSaveCount = self.data.descSaveCount = 0;
			}
		} catch (e) {
			if (!cleanupDue) self.descSaveCount = self.data.descSaveCount = previousSaveCount;
			if (promoted) {
				if (primaryMoved) {
					if (!restoreBackup(primaryPath, backupPath))
						throw new Error(errorMessageText(e) + "\n\n" + str.errSettingsRestoreBackup + "\n" + backupPath);
				} else {
					var failedPrimary = new File(primaryPath);
					if (failedPrimary.exists) try { failedPrimary.remove(); } catch (_) { }
				}
			}
			var staleTemp = new File(tempPath);
			if (staleTemp.exists) try { staleTemp.remove(); } catch (_) { }
			throw e;
		}
	};
	this.resetProfile = function (workflowId) {
		var profiles = profileStore("workflowProfiles");
		if (workflowId !== undefined && workflowId !== null) delete profiles[String(workflowId)];
	};
	this.resetForgeProfile = function (presetId) {
		var profiles = profileStore("forgeProfiles");
		if (presetId !== undefined && presetId !== null) delete profiles[String(presetId)];
	};
	this.getProfile = function (workflowId) {
		var profiles = profileStore("workflowProfiles"), profile = profiles[workflowId];
		if (!isObjectMap(profile)) profile = profiles[workflowId] = {
			relativePath: "", values: {}, selectedPromptPresets: { positive: "", negative: "" }, visibleControls: null,
			bindingOverrides: { input: "", mask: "", references: [], referencesConfigured: false, emptyInputs: [], output: "", sizeMode: "auto", size: "" },
			referenceFiles: {}, sizeMultiple: self.sizeMultiple,
			autoResize: self.autoResize, resizePreset: presets.normalizeResizeName("", self.resizePresets),
			outputFormat: "jpg", manualScale: 1, workflowInformationSeen: false,
			schemaCache: null, schemaCacheStamp: null, schemaCacheVersion: 0, schemaCacheUsed: 0
		};
		normalizeBaseProfile(profile);
		if (!isObjectMap(profile.bindingOverrides))
			profile.bindingOverrides = { input: "", mask: "", references: [], referencesConfigured: false, emptyInputs: [], output: "", sizeMode: "auto", size: "" };
		var bindings = profile.bindingOverrides;
		if (bindings.input === undefined) bindings.input = "";
		if (bindings.mask === undefined) bindings.mask = "";
		if (!(bindings.references instanceof Array)) bindings.references = [];
		bindings.referencesConfigured = bindings.referencesConfigured === true;
		if (!(bindings.emptyInputs instanceof Array)) bindings.emptyInputs = [];
		if (bindings.output === undefined) bindings.output = "";
		if (bindings.sizeMode !== "source_image" && bindings.sizeMode !== "binding") bindings.sizeMode = "auto";
		if (bindings.size === undefined || bindings.sizeMode != "binding") bindings.size = "";
		if (!isObjectMap(profile.referenceFiles)) profile.referenceFiles = {};
		profile.outputFormat = normalizeOutputFormat(profile.outputFormat);
		profile.workflowInformationSeen = profile.workflowInformationSeen === true;
		if (profile.sizeMultiple === undefined) profile.sizeMultiple = self.sizeMultiple;
		profile.sizeMultiple = clamp(parseInt(profile.sizeMultiple, 10) || self.sizeMultiple, 1, 256);
		return profile;
	};
	this.getForgeProfile = function (presetId) {
		var profiles = profileStore("forgeProfiles"), profile = profiles[presetId];
		if (!isObjectMap(profile)) profile = profiles[presetId] = {
			values: {}, selectedPromptPresets: { positive: "", negative: "" }, visibleControls: null, selectedLoras: [], imageStitchInputs: ["", "", ""],
			sizeMultiple: null, autoResize: self.autoResize,
			resizePreset: presets.normalizeResizeName("", self.resizePresets), manualScale: 1,
			lorasInitialized: false
		};
		normalizeBaseProfile(profile);
		if (profile.autoResize === undefined) profile.autoResize = self.autoResize;
		if (!(profile.selectedLoras instanceof Array)) profile.selectedLoras = [];
		profile.selectedLoras = normalizeForgeLoraSelection([], profile.selectedLoras);
		if (profile.lorasInitialized === undefined)
			profile.lorasInitialized = true;
		else
			profile.lorasInitialized = !!profile.lorasInitialized;
		if (!(profile.imageStitchInputs instanceof Array)) profile.imageStitchInputs = ["", "", ""];
		while (profile.imageStitchInputs.length < 3) profile.imageStitchInputs.push("");
		if (profile.imageStitchInputs.length > 3) profile.imageStitchInputs = profile.imageStitchInputs.slice(0, 3);
		for (var imageIndex = 0; imageIndex < profile.imageStitchInputs.length; imageIndex++)
			if (typeof profile.imageStitchInputs[imageIndex] != "string") profile.imageStitchInputs[imageIndex] = "";
		if (profile.sizeMultiple === undefined || profile.sizeMultiple === null || profile.sizeMultiple === "") {
			profile.sizeMultiple = null;
		} else {
			var parsedSizeMultiple = parseInt(profile.sizeMultiple, 10);
			profile.sizeMultiple = isNaN(parsedSizeMultiple) ? null : clamp(parsedSizeMultiple, 1, 256);
		}
		return profile;
	};
	this.getPromptPresetStore = function (context) {
		return presets.promptStore(self, context);
	};
	this.copySharedLibrariesFrom = function (sourceConfig) {
		self.referenceHistory = self.data.referenceHistory = cloneObj(sourceConfig && sourceConfig.referenceHistory instanceof Array ? sourceConfig.referenceHistory : []);
		self.cleanReferenceHistory();
		self.promptPresets = self.data.promptPresets = cloneObj(
			sourceConfig && sourceConfig.promptPresets ? sourceConfig.promptPresets : presets.defaultPrompt()
		);
		self.getPromptPresetStore("positive");
		self.getPromptPresetStore("negative");
	};
	this.copySharedLibrariesTo = function (targetConfig) {
		if (!targetConfig) return;
		targetConfig.referenceHistory = targetConfig.data.referenceHistory = cloneObj(self.referenceHistory instanceof Array ? self.referenceHistory : []);
		targetConfig.cleanReferenceHistory();
		targetConfig.promptPresets = targetConfig.data.promptPresets = cloneObj(self.promptPresets || presets.defaultPrompt());
		targetConfig.getPromptPresetStore("positive");
		targetConfig.getPromptPresetStore("negative");
	};
	this.copyPythonRuntimeSettingsFrom = function (sourceConfig) {
		if (!sourceConfig) return;
		self.pythonIdleTimeout = self.data.pythonIdleTimeout = sourceConfig.pythonIdleTimeout;
		self.backendMonitorInterval = self.data.backendMonitorInterval = sourceConfig.backendMonitorInterval;
	};
	this.copyPythonRuntimeSettingsTo = function (targetConfig) {
		if (!targetConfig) return;
		targetConfig.pythonIdleTimeout = targetConfig.data.pythonIdleTimeout = self.pythonIdleTimeout;
		targetConfig.backendMonitorInterval = targetConfig.data.backendMonitorInterval = self.backendMonitorInterval;
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
		profile.schemaCacheUsed = (new Date()).getTime();
		return profile.schemaCache;
	};
	this.cacheSchema = function (schema, workflow) {
		if (!schema || !schema.workflow_id) return;
		var profile = self.getProfile(schema.workflow_id);
		profile.relativePath = schema.relative_path || (workflow ? workflow.relative_path : profile.relativePath) || "";
		profile.schemaCache = schema;
		profile.schemaCacheStamp = workflowStamp(profile.relativePath);
		profile.schemaCacheVersion = APP.cache.schemaVersion;
		profile.schemaCacheUsed = (new Date()).getTime();
		var cached = [], workflowId;
		for (workflowId in self.workflowProfiles) if (self.workflowProfiles.hasOwnProperty(workflowId)) {
			var cachedProfile = self.workflowProfiles[workflowId];
			if (cachedProfile && cachedProfile.schemaCache)
				cached.push({ id: workflowId, used: Number(cachedProfile.schemaCacheUsed) || 0 });
		}
		cached.sort(function (a, b) { return b.used - a.used; });
		for (var i = APP.cache.maxWorkflowSchemas; i < cached.length; i++) {
			var staleProfile = self.workflowProfiles[cached[i].id];
			staleProfile.schemaCache = null;
			staleProfile.schemaCacheStamp = null;
			staleProfile.schemaCacheVersion = 0;
			staleProfile.schemaCacheUsed = 0;
		}
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
			brushOpacity: 60,
			generationTimeout: 1200,
			pythonIdleTimeout: 15 * 60,
			backendMonitorInterval: 5,
			workflowProfiles: {},
			forgeProfiles: {},
			workflowCatalog: [],
			forgeCatalog: [],
			referenceHistory: [],
			promptPresets: presets.defaultPrompt(),
			descSaveCount: 0
		};
	}
	function mergeDefaults(defaults, loaded) {
		if (!isObjectMap(loaded)) return defaults;
		for (var key in loaded) if (loaded.hasOwnProperty(key)) {
			if (isObjectMap(loaded[key]) && isObjectMap(defaults[key]))
				defaults[key] = mergeDefaults(defaults[key], loaded[key]);
			else defaults[key] = loaded[key];
		}
		return defaults;
	}
}
// ---
// НИЗКОУРОВНЕВЫЕ ACTION MANAGER ОПЕРАЦИИ PHOTOSHOP
// Обёртка над ActionReference/ActionDescriptor для документа, слоя и приложения.
// ---
function AM(target, order) {
	var AR = ActionReference, AD = ActionDescriptor;
	target = target ? s2t(target) : null;
	this.getProperty = function (property, descriptorMode, id, indexMode) {
		var propertyId = s2t(property), ref = new AR();
		ref.putProperty(s2t("property"), propertyId);
		if (id !== undefined && id !== null) {
			if (indexMode) ref.putIndex(target, id); else ref.putIdentifier(target, id);
		} else ref.putEnumerated(target, s2t("ordinal"), order ? s2t(order) : s2t("targetEnum"));
		var desc = executeActionGet(ref);
		return descriptorMode ? desc : getDescValue(desc, propertyId);
	};
	this.hasProperty = function (property, id, indexMode) {
		var propertyId = s2t(property), ref = new AR();
		ref.putProperty(s2t("property"), propertyId);
		if (id !== undefined && id !== null) {
			if (indexMode) ref.putIndex(target, id); else ref.putIdentifier(target, id);
		} else ref.putEnumerated(target, s2t("ordinal"), s2t("targetEnum"));
		try { return executeActionGet(ref).hasKey(propertyId); } catch (_) { return false; }
	};
	this.setProperty = function (property, value) {
		var propertyId = s2t(property), ref = new AR();
		ref.putProperty(s2t("property"), propertyId);
		ref.putEnumerated(target, s2t("ordinal"), s2t("targetEnum"));
		var desc = new AD();
		desc.putReference(s2t("null"), ref);
		desc.putObject(s2t("to"), propertyId, value);
		executeAction(s2t("set"), desc, DialogModes.NO);
	};
	this.descToObject = function (desc) {
		var res = {}, i;
		for (i = 0; i < desc.count; i++) {
			var key = desc.getKey(i);
			res[t2s(key)] = getDescValue(desc, key);
		}
		return res;
	};
	this.makeSelection = function (bounds) {
		var ref = new AR(); ref.putProperty(s2t("channel"), s2t("selection"));
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		var rectangle = new AD();
		rectangle.putUnitDouble(s2t("top"), s2t("pixelsUnit"), bounds.top);
		rectangle.putUnitDouble(s2t("left"), s2t("pixelsUnit"), bounds.left);
		rectangle.putUnitDouble(s2t("bottom"), s2t("pixelsUnit"), bounds.bottom);
		rectangle.putUnitDouble(s2t("right"), s2t("pixelsUnit"), bounds.right);
		desc.putObject(s2t("to"), s2t("rectangle"), rectangle);
		executeAction(s2t("set"), desc, DialogModes.NO);
	};
	this.makeSelectionFromLayer = function (channel, id) {
		var selectionRef = new AR(); selectionRef.putProperty(s2t("channel"), s2t("selection"));
		var desc = new AD(); desc.putReference(s2t("null"), selectionRef);
		var sourceRef = new AR(); sourceRef.putEnumerated(s2t("channel"), s2t("channel"), s2t(channel));
		if (id !== undefined && id !== null) sourceRef.putIdentifier(s2t("layer"), id);
		desc.putReference(s2t("to"), sourceRef);
		executeAction(s2t("set"), desc, DialogModes.NO);
	};
	this.deselect = function () {
		var ref = new AR(); ref.putProperty(s2t("channel"), s2t("selection"));
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		desc.putEnumerated(s2t("to"), s2t("ordinal"), s2t("none"));
		executeAction(s2t("set"), desc, DialogModes.NO);
	};
	this.quickMask = function (eventName) {
		var ref = new AR(); ref.putProperty(s2t("property"), s2t("quickMask"));
		ref.putEnumerated(s2t("document"), s2t("ordinal"), s2t("targetEnum"));
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		executeAction(s2t(eventName), desc, DialogModes.NO);
	};
	this.makeLayer = function (name) {
		var ref = new AR(); ref.putClass(s2t("layer"));
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		var layerDescriptor = new AD(); layerDescriptor.putString(s2t("name"), name);
		desc.putObject(s2t("using"), s2t("layer"), layerDescriptor);
		executeAction(s2t("make"), desc, DialogModes.NO);
	};
	this.makeSelectionMask = function () {
		var desc = new AD(); desc.putClass(s2t("new"), s2t("channel"));
		var ref = new AR(); ref.putEnumerated(s2t("channel"), s2t("channel"), s2t("mask"));
		desc.putReference(s2t("at"), ref);
		desc.putEnumerated(s2t("using"), s2t("userMask"), s2t("revealSelection"));
		executeAction(s2t("make"), desc, DialogModes.NO);
	};
	this.flatten = function () {
		executeAction(s2t("flattenImage"), undefined, DialogModes.NO);
	};
	this.mergeVisible = function () {
		executeAction(s2t("mergeVisible"), undefined, DialogModes.NO);
	};
	this.crop = function (deletePixels) {
		var desc = new AD(); desc.putBoolean(s2t("delete"), !!deletePixels);
		executeAction(s2t("crop"), desc, DialogModes.NO);
	};
	this.imageSize = function (width, height) {
		var desc = new AD();
		desc.putUnitDouble(s2t("width"), s2t("pixelsUnit"), width);
		desc.putUnitDouble(s2t("height"), s2t("pixelsUnit"), height);
		desc.putEnumerated(s2t("interpolation"), s2t("interpolationType"), s2t("automaticInterpolation"));
		executeAction(s2t("imageSize"), desc, DialogModes.NO);
	};
	this.saveAPNGCopy = function (file) {
		var pngOptions = new AD();
		pngOptions.putEnumerated(s2t("method"), s2t("PNGMethod"), s2t("quick"));
		pngOptions.putEnumerated(s2t("PNGInterlaceType"), s2t("PNGInterlaceType"), s2t("PNGInterlaceNone"));
		pngOptions.putEnumerated(s2t("PNGFilter"), s2t("PNGFilter"), s2t("PNGFilterAdaptive"));
		pngOptions.putInteger(s2t("compression"), 6);
		var desc = new AD();
		desc.putObject(s2t("as"), s2t("PNGFormat"), pngOptions);
		desc.putPath(s2t("in"), file);
		desc.putBoolean(s2t("copy"), true);
		executeAction(s2t("save"), desc, DialogModes.NO);
	};
	this.copyPixels = function () {
		var desc = new AD(); desc.putString(s2t("copyHint"), "pixels");
		executeAction(s2t("copyEvent"), desc, DialogModes.NO);
	};
	this.pastePixels = function () {
		var desc = new AD();
		desc.putEnumerated(s2t("antiAlias"), s2t("antiAliasType"), s2t("antiAliasNone"));
		desc.putClass(s2t("as"), s2t("pixel"));
		executeAction(s2t("paste"), desc, DialogModes.NO);
	};
	this.saveACopy = function (file) {
		var jpegOptions = new AD();
		jpegOptions.putInteger(s2t("extendedQuality"), 12);
		jpegOptions.putEnumerated(s2t("matteColor"), s2t("matteColor"), s2t("none"));
		var desc = new AD();
		desc.putObject(s2t("as"), s2t("JPEG"), jpegOptions);
		desc.putPath(s2t("in"), file);
		desc.putBoolean(s2t("copy"), true);
		executeAction(s2t("save"), desc, DialogModes.NO);
	};
	this.place = function (file) {
		var desc = new AD(); desc.putPath(s2t("null"), file); desc.putBoolean(s2t("linked"), false);
		executeAction(s2t("placeEvent"), desc, DialogModes.NO);
	};
	this.transform = function (widthPercent, heightPercent, offsetX, offsetY) {
		var desc = new AD();
		desc.putEnumerated(s2t("freeTransformCenterState"), s2t("quadCenterState"), s2t("QCSAverage"));
		var offset = new AD();
		offset.putUnitDouble(s2t("horizontal"), s2t("pixelsUnit"), offsetX || 0);
		offset.putUnitDouble(s2t("vertical"), s2t("pixelsUnit"), offsetY || 0);
		desc.putObject(s2t("offset"), s2t("offset"), offset);
		desc.putUnitDouble(s2t("width"), s2t("percentUnit"), widthPercent);
		desc.putUnitDouble(s2t("height"), s2t("percentUnit"), heightPercent);
		executeAction(s2t("transform"), desc, DialogModes.NO);
	};
	this.rasterize = function () {
		var ref = new AR(); ref.putEnumerated(s2t("layer"), s2t("ordinal"), s2t("targetEnum"));
		var desc = new AD(); desc.putReference(s2t("target"), ref);
		executeAction(s2t("rasterizePlaced"), desc, DialogModes.NO);
	};
	this.selectLayersByIDs = function (ids) {
		var ref = new AR();
		for (var i = 0; i < ids.length; i++) ref.putIdentifier(s2t("layer"), ids[i]);
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		executeAction(s2t("select"), desc, DialogModes.NO);
	};
	function setSelectedLayersVisibility(actionName) {
		var ref = new AR(); ref.putEnumerated(s2t("layer"), s2t("ordinal"), s2t("targetEnum"));
		var list = new ActionList(); list.putReference(ref);
		var desc = new AD(); desc.putList(s2t("null"), list);
		executeAction(s2t(actionName), desc, DialogModes.NO);
	}
	this.hideSelectedLayers = function () { setSelectedLayersVisibility("hide"); };
	this.showSelectedLayers = function () { setSelectedLayersVisibility("show"); };
	this.setName = function (name) {
		var ref = new AR(); ref.putEnumerated(s2t("layer"), s2t("ordinal"), s2t("targetEnum"));
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		var layerDescriptor = new AD(); layerDescriptor.putString(s2t("name"), name);
		desc.putObject(s2t("to"), s2t("layer"), layerDescriptor);
		executeAction(s2t("set"), desc, DialogModes.NO);
	};
	this.deleteLayer = function (id) {
		var ref = new AR();
		if (id !== undefined && id !== null) ref.putIdentifier(s2t("layer"), id);
		else ref.putEnumerated(s2t("layer"), s2t("ordinal"), s2t("targetEnum"));
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		executeAction(s2t("delete"), desc, DialogModes.NO);
	};
	this.selectChannel = function (channel) {
		var ref = new AR(); ref.putEnumerated(s2t("channel"), s2t("channel"), s2t(channel));
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		executeAction(s2t("select"), desc, DialogModes.NO);
	};
	this.selectBrush = function () {
		var ref = new AR(); ref.putClass(s2t("paintbrushTool"));
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		executeAction(s2t("select"), desc, DialogModes.NO);
	};
	this.resetSwatches = function () {
		var ref = new AR(); ref.putProperty(s2t("color"), s2t("colors"));
		var desc = new AD(); desc.putReference(s2t("null"), ref);
		executeAction(s2t("reset"), desc, DialogModes.NO);
	};
	this.setBrushOpacity = function (opacity) {
		var property = s2t("currentToolOptions"),
			ref = new AR(); ref.putProperty(s2t("property"), property);
		ref.putEnumerated(s2t("application"), s2t("ordinal"), s2t("targetEnum"));
		var options = executeActionGet(ref).getObjectValue(property);
		options.putInteger(s2t("opacity"), opacity);
		var toolRef = new AR(); toolRef.putClass(s2t("paintbrushTool"));
		var desc = new AD(); desc.putReference(s2t("target"), toolRef);
		desc.putObject(s2t("to"), s2t("target"), options);
		executeAction(s2t("set"), desc, DialogModes.NO);
	};
	function getDescValue(desc, key) {
		switch (desc.getType(key)) {
			case DescValueType.OBJECTTYPE: return { type: t2s(desc.getObjectType(key)), value: desc.getObjectValue(key) };
			case DescValueType.LISTTYPE: return desc.getList(key);
			case DescValueType.REFERENCETYPE: return desc.getReference(key);
			case DescValueType.BOOLEANTYPE: return desc.getBoolean(key);
			case DescValueType.STRINGTYPE: return desc.getString(key);
			case DescValueType.INTEGERTYPE: return desc.getInteger(key);
			case DescValueType.LARGEINTEGERTYPE: return desc.getLargeInteger(key);
			case DescValueType.DOUBLETYPE: return desc.getDouble(key);
			case DescValueType.ALIASTYPE: return desc.getPath(key);
			case DescValueType.CLASSTYPE: return desc.getClass(key);
			case DescValueType.UNITDOUBLE: return desc.getUnitDoubleValue(key);
			case DescValueType.ENUMERATEDTYPE: return { type: t2s(desc.getEnumerationType(key)), value: t2s(desc.getEnumerationValue(key)) };
		}
		return null;
	}
}
// Хранит последние длительности генерации в custom options Photoshop и
// использует среднее как ориентир следующего progress bar.
function Delay() {
	var settingsObj = this;
	this.getDelay = function (workflowId) {
		try { var desc = getCustomOptions(APP.uuid); } catch (_) { }
		if (desc != undefined) descriptorCodec.readInto(settingsObj, desc);
		if (settingsObj[workflowId]) {
			var sum = 0;
			for (var i = 0; i < settingsObj[workflowId].length; i++) sum += settingsObj[workflowId][i];
			sum = Math.round(sum / settingsObj[workflowId].length);
			return sum < 1000 ? 1000 : sum;
		}
		return GENERATION_RUN_DEFAULT_EXPECTED_MS;
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
// ---
// ЛОКАЛИЗАЦИЯ UI
// Значения Locale передаются ScriptUI напрямую: $.localize = true выполняет
// выбор языка автоматически, поэтому явный localize() здесь не используется.
// ---
function Locale() {
	var localized = {
		all: ["Все", "All"], recordSettingsToAction: ["Записывать настройки в экшен", "Record settings to action"], automatic: ["Автоматически", "Automatic"],
		autoResize: ["Автомасштаб", "Auto resize"], brushSettings: ["Настройки кисти", "Brush settings"], browse: ["Обзор…", "Browse…"],
		connectionSettings: ["Подключение", "Connection"], pythonServerSettings: ["Python-сервер", "Python server"],
		pythonApiVersion: ["Версия Python API:", "Python API version:"],
		pythonIdleTimeout: ["Остановка после простоя, мин (0 — выкл.):", "Stop after idle, min (0 = off):"],
		backendMonitorInterval: ["Проверка бэкендов в фоне, с:", "Background backend check, s:"],
		errSettingsSaveAfterError: ["Операция завершилась с ошибкой, и настройки сохранить не удалось:", "The operation failed and the settings could not be saved:"],
		errSettingsSaveAfterGeneration: ["Результат создан, но настройки сохранить не удалось:", "The result was created, but the settings could not be saved:"],
		errSettingsReadFile: ["Не удалось прочитать файл настроек.", "Could not read the settings file."],
		errSettingsWriteFile: ["Не удалось записать временный файл настроек.", "Could not write the temporary settings file."],
		errSettingsBackupFile: ["Не удалось сохранить предыдущий файл настроек как резервную копию.", "Could not preserve the previous settings file as a backup."],
		errSettingsReplaceFile: ["Не удалось заменить основной файл настроек проверенным временным файлом.", "Could not replace the main settings file with the verified temporary file."],
		errSettingsRestoreBackup: ["Не удалось восстановить резервную копию файла настроек.", "Could not restore the settings backup."],
		errSettingsUnreadable: ["Основной файл настроек повреждён или недоступен. Настройки по умолчанию не были загружены, чтобы не перезаписать пользовательские данные.", "The main settings file is damaged or unavailable. Defaults were not loaded, so the user's data will not be overwritten."],
		settingsBackupRecovered: ["Основной файл настроек прочитать не удалось. Загружена резервная копия:", "The main settings file could not be read. The backup was loaded:"],
		settingsPrimaryReadError: ["Ошибка основного файла:", "Main-file error:"],
		noAvailableValues: ["нет доступных значений; генерация отключена", "no available values; generation is disabled"],
		invalidForgeSchema: ["Ошибка схемы Forge", "Invalid Forge schema"], unknownFile: ["неизвестный файл", "unknown file"],
		errApiConnection: ["Нет соединения с Python API.", "Cannot connect to Python API."],
		errApiTimeout: ["Превышено время ожидания ответа Python API. Лог: %LOCALAPPDATA%\\" + APP.tempFolder + "\\" + API_FILE + ".log", "Python API response timed out. Log: %LOCALAPPDATA%\\" + APP.tempFolder + "\\" + API_FILE + ".log"],
		errApiInvalidAnswer: ["Python API вернул повреждённый ответ.", "Python API returned an invalid response."],
		errApiProtocolA: ["Запущена несовместимая версия протокола Python API (", "An incompatible Python API protocol is running ("],
		errApiProtocolB: ["). Ожидается версия ", "). Expected protocol: "], errEmptyApiAnswer: ["Пустой ответ Python API.", "Empty response from Python API."],
		errListenerPort: ["Не удалось открыть listener-порт ", "Cannot open listener port "],
		errMode: [APP.name + " работает только с RGB-документами.", APP.name + " works only with RGB documents."],
		errSelectionTooSmall: ["Выделение или документ слишком малы. Минимальный размер каждой стороны:", "The selection or document is too small. Minimum size for each side:"],
		errNoResult: ["Бэкенд не вернул результат.", "The backend returned no result."],
		errPlacedBounds: ["Не удалось определить размер вставленного слоя.", "Could not determine placed layer bounds."],
		errPythonMissingA: ["Не найден ", "Could not find "],
		errPythonMissingB: [".pyw или .py рядом с JSX либо в подпапке lib.", ".pyw or .py next to JSX or in the lib subfolder."],
		errPythonExecute: ["Windows не смог запустить файл Python. Проверьте установку Python и ассоциацию файлов .pyw/.py:", "Windows could not launch the Python file. Check the Python installation and .pyw/.py file association:"],
		errPythonInstallTimeout: ["Установка зависимостей Python не завершилась за две минуты. Python может продолжать установку в фоне; повторите запуск скрипта позже.", "Python dependency installation did not finish within two minutes. Python may continue installing in the background; run the script again later."],
		errPythonStartupDetails: ["Python API завершил запуск с ошибкой.", "Python API startup failed."],
		pythonLog: ["Лог: ", "Log: "],
		errPythonStartA: ["Python API не запустился на ", "Python API did not start on "], errResultFile: ["Файл результата не найден:", "Result file not found:"],
		errSaveJpeg: ["Photoshop не смог сохранить временный JPEG.", "Photoshop could not save the temporary JPEG."],
		errSavePng: ["Photoshop не смог сохранить временный PNG с маской.", "Photoshop could not save the temporary PNG with a mask."],
		errSaveMask: ["Photoshop не смог сохранить временную маску PNG.", "Photoshop could not save the temporary PNG mask."],
		errInpaintMaskMissing: ["Для параметра «Маска inpaint» не найден подходящий вариант. В настройках workflow выберите MASK основной ноды LoadImage или ноду LoadImageMask.", "No suitable option was found for Inpaint mask. In Workflow settings, select the main LoadImage MASK or a LoadImageMask node."],
		errInpaintInputDisconnected: ["MASK основной ноды LoadImage не используется в workflow. Подключите выход MASK к inpaint-ветке в ComfyUI или выберите другой вариант для параметра «Маска inpaint».", "The main LoadImage MASK is not used by the workflow. Connect its MASK output to the inpaint branch in ComfyUI or select another Inpaint mask option."],
		errInpaintNodeDisconnected: ["MASK выбранной ноды LoadImageMask не используется в workflow. Подключите её выход MASK к inpaint-ветке в ComfyUI или выберите другой вариант для параметра «Маска inpaint».", "The selected LoadImageMask MASK is not used by the workflow. Connect its MASK output to the inpaint branch in ComfyUI or select another Inpaint mask option."],
		errSelectedWorkflowMissing: ["Выбранный workflow больше не найден.", "The selected workflow can no longer be found."],
		selected_workflow_missing: ["Выбранный workflow больше не найден в указанной папке. Обновите список workflow или выберите другой файл.", "The selected workflow is no longer present in the configured folder. Refresh the workflow list or select another file."],
		workflow_json_invalid: ["Ошибка JSON в workflow %1, строка %2, столбец %3: %4", "JSON error in workflow %1, line %2, column %3: %4"],
		workflow_root_invalid: ["Корень API-workflow %1 должен быть JSON-объектом.", "The API-workflow root in %1 must be a JSON object."],
		workflow_target_node_missing: ["Нода #%1, используемая текущими настройками workflow, больше не существует. Повторно проанализируйте workflow.", "Node #%1 used by the current workflow settings no longer exists. Reanalyze the workflow."],
		workflow_target_input_missing: ["В ноде #%1 больше нет входа %2, используемого текущими настройками workflow. Повторно проанализируйте workflow.", "Node #%1 no longer has input %2 used by the current workflow settings. Reanalyze the workflow."],
		api_workflow_required: ["Выбран обычный JSON интерфейса ComfyUI. Откройте workflow в ComfyUI и выполните Workflow/File → Export (API).", "The selected JSON uses the regular ComfyUI UI format. Open it in ComfyUI and choose Workflow/File → Export (API)."],
		workflow_empty: ["Выбранный workflow пуст.", "The selected workflow is empty."],
		invalid_api_nodes: ["В API-workflow найдены некорректные ноды без class_type или inputs: %1", "The API workflow contains invalid nodes without class_type or inputs: %1"],
		workflow_not_ready: ["Workflow не готов к запуску. Откройте настройки workflow и исправьте перечисленные проблемы.", "The workflow is not ready to run. Open Workflow settings and correct the listed problems."],
		translator_unavailable: ["Перевод prompt недоступен: не удалось загрузить deep-translator. Подробности записаны в журнал Python API.", "Prompt translation is unavailable because deep-translator could not be loaded. See the Python API log for details."],
		inpaint_mask_missing: ["Для параметра «Маска inpaint» не найден подходящий вариант. В настройках workflow выберите MASK основной ноды LoadImage или ноду LoadImageMask.", "No suitable option was found for Inpaint mask. In Workflow settings, select the main LoadImage MASK or a LoadImageMask node."],
		inpaint_mask_changed: ["Настройка параметра «Маска inpaint» изменилась. Снова откройте главное окно скрипта.", "The Inpaint mask configuration changed. Reopen the main script window."],
		inpaint_main_mask_unused: ["MASK основной ноды LoadImage не используется в workflow. Подключите выход MASK к inpaint-ветке в ComfyUI или выберите другой вариант параметра «Маска inpaint».", "The main LoadImage MASK is not used by the workflow. Connect its MASK output to the inpaint branch in ComfyUI or select another Inpaint mask option."],
		inpaint_node_mask_unused: ["MASK выбранной ноды LoadImageMask не используется в workflow. Подключите её выход MASK к inpaint-ветке в ComfyUI или выберите другой вариант параметра «Маска inpaint».", "The selected LoadImageMask MASK is not used by the workflow. Connect its MASK output to the inpaint branch in ComfyUI or select another Inpaint mask option."],
		load_image_mask_unavailable: ["В ComfyUI недоступна стандартная нода LoadImageMask, необходимая для маски Photoshop. Обновите ComfyUI или добавьте в workflow отдельную ноду LoadImageMask.", "The standard LoadImageMask node required for the Photoshop mask is unavailable in ComfyUI. Update ComfyUI or add a separate LoadImageMask node to the workflow."],
		main_mask_source_missing: ["Основная нода для пункта Main LoadImage MASK больше не существует. Повторно проанализируйте workflow.", "The source node for Main LoadImage MASK no longer exists. Reanalyze the workflow."],
		selected_mask_no_longer_used: ["Выбранная «Маска inpaint» больше не используется в workflow. Повторно проанализируйте workflow или исправьте соединения MASK в ComfyUI.", "The selected Inpaint mask is no longer used by the workflow. Reanalyze the workflow or fix the MASK connections in ComfyUI."],
		selected_size_rejected: ["Выбранные «Поля width / height» не принимают размер изображения Photoshop:\n• %1\n\nВ настройках workflow выберите «Размер входного изображения», «Автоматически» или другую пару полей.", "The selected Width / height fields cannot accept the Photoshop image size:\n• %1\n\nIn Workflow settings, choose Input image size, Automatic, or another field pair."],
		output_image_missing_from_history: ["Workflow завершён, но выбранное «Выходное изображение» (нода #%1) отсутствует в history ComfyUI.", "The workflow completed, but the selected Output image (node #%1) is missing from ComfyUI history."],
		output_image_not_returned: ["Выбранное «Выходное изображение» (нода #%1) не вернуло изображение. Выберите Save Image или Preview Image с меткой #PS-OUTPUT.", "The selected Output image (node #%1) did not return an image. Select Save Image or Preview Image tagged #PS-OUTPUT."],
		forge_schema_folder_missing: ["Папка схем Forge не существует: %1", "The Forge schema folder does not exist: %1"],
		forge_schema_folder_not_selected: ["Папка схем Forge не найдена. Выберите её в настройках скрипта.", "The Forge schema folder was not found. Select it in the script settings."],
		forge_schema_missing: ["Выбранная схема Forge больше не найдена: %1", "The selected Forge schema can no longer be found: %1"],
		forge_schema_json_invalid: ["Некорректный JSON в схеме Forge %1, строка %2, столбец %3: %4", "Invalid JSON in Forge schema %1, line %2, column %3: %4"],
		forge_schema_root_invalid: ["Схема Forge %1 должна быть JSON-объектом.", "Forge schema %1 must be a JSON object."],
		forge_schema_kind_invalid: ["Файл %1 не является схемой Forge img2img helper.", "File %1 is not an img2img helper Forge schema."],
		forge_schema_version_invalid: ["Схема Forge %1 использует неподдерживаемую schema_version %2 (поддерживается %3).", "Forge schema %1 uses unsupported schema_version %2 (supported: %3)."],
		forge_schema_inheritance_cycle: ["Обнаружен цикл наследования схем Forge: %1", "Circular Forge schema inheritance was detected: %1"],
		forge_schema_base_missing: ["Не найдена базовая схема Forge: %1", "Base Forge schema was not found: %1"],
		forge_schema_order_invalid: ["Некорректное значение order в схеме Forge %1: %2", "Invalid order value in Forge schema %1: %2"],
		workflow_save_no_values: ["В workflow нет видимых параметров для сохранения.", "There are no visible workflow values to save."],
		forge_save_no_values: ["В схеме Forge нет видимых параметров для сохранения.", "There are no visible Forge schema values to save."],
		save_destination_missing: ["Файл назначения не выбран. Исходный JSON не изменён.", "No destination file was selected. The source JSON was not changed."],
		forge_image_stitch_unsupported: ["Выбранная схема Forge не поддерживает ImageStitch.", "The selected Forge schema does not support ImageStitch."],
		forge_processing_mode_required: ["Выберите хотя бы один режим обработки Forge.", "Select at least one Forge processing mode."],
		generation_already_running: ["Предыдущая генерация ещё не завершена.", "The previous generation has not finished yet."],
		workflow_folder_not_selected: ["Папка API-workflow не выбрана. Укажите её в настройках скрипта.", "The API-workflow folder is not selected. Choose it in the script settings."],
		workflow_folder_missing: ["Папка API-workflow не существует: %1", "The API-workflow folder does not exist: %1"],
		workflow_path_not_folder: ["Путь API-workflow не является папкой: %1", "The API-workflow path is not a folder: %1"],
		image_stitch_decode_failed: ["ImageStitch не удалось прочитать выбранное изображение: %1", "ImageStitch could not read the selected image: %1"],
		image_stitch_prepare_failed: ["ImageStitch не удалось подготовить выбранное изображение: %1", "ImageStitch could not prepare the selected image: %1"],
		workflow_save_invalid_bindings: ["Workflow нельзя сохранить: текущие назначения нод некорректны. Исправьте их в настройках workflow.", "The workflow cannot be saved because the current node assignments are invalid. Correct them in Workflow settings."],
		workflow_save_field_missing: ["Параметр workflow %1 отсутствует в текущем анализе. Повторно проанализируйте workflow.", "Workflow parameter %1 is missing from the current analysis. Reanalyze the workflow."],
		workflow_save_field_no_targets: ["Для параметра workflow %1 не найдены целевые поля. Повторно проанализируйте workflow и проверьте назначения.", "No target fields were found for workflow parameter %1. Reanalyze the workflow and check its assignments."],
		workflow_save_json_required: ["Workflow необходимо сохранить как JSON-файл:\n%1", "The workflow must be saved as a JSON file:\n%1"],
		workflow_save_write_failed: ["Не удалось записать workflow JSON:\n%1\n\n%2\n\nПроверьте права доступа к файлу и папке.", "Could not write workflow JSON:\n%1\n\n%2\n\nCheck file and folder permissions."],
		forge_save_field_missing: ["Параметр Forge %1 отсутствует в выбранной схеме.", "Forge parameter %1 is absent from the selected schema."],
		forge_save_json_required: ["Схему Forge необходимо сохранить как JSON-файл:\n%1", "The Forge schema must be saved as a JSON file:\n%1"],
		forge_save_write_failed: ["Не удалось записать JSON-схему Forge:\n%1\n\n%2\n\nПроверьте права доступа к файлу и папке.", "Could not write Forge schema JSON:\n%1\n\n%2\n\nCheck file and folder permissions."],
		translate_failed: ["Не удалось перевести промпт:\n%1", "Could not translate the prompt:\n%1"],
		forge_field_invalid_value: ["Значение «%1» недоступно для поля Forge %2. Обновите данные схемы или выберите другое значение.", "Value “%1” is unavailable for Forge field %2. Refresh the schema data or select another value."],
		forge_field_no_values: ["Для поля Forge %1 нет доступных значений. Обновите данные схемы.", "Forge field %1 has no available values. Refresh the schema data."],
		forge_field_list_expected: ["Поле Forge %1 должно содержать список значений.", "Forge field %1 expects a list of values."],
		forge_field_integer_expected: ["Поле Forge %1 должно содержать целое число; получено: %2.", "Forge field %1 expects an integer; received: %2."],
		forge_field_number_expected: ["Поле Forge %1 должно содержать число; получено: %2.", "Forge field %1 expects a number; received: %2."],
		forge_field_finite_expected: ["Поле Forge %1 должно содержать конечное число; получено: %2.", "Forge field %1 expects a finite number; received: %2."],
		forge_field_non_finite: ["Поле Forge %1 сформировало недопустимое числовое значение.", "Forge field %1 produced an invalid numeric value."],
		workflow_field_invalid_value: ["Значение «%1» недоступно для поля workflow %2. Повторно проанализируйте workflow или выберите другое значение.", "Value “%1” is unavailable for workflow field %2. Reanalyze the workflow or select another value."],
		workflow_field_integer_expected: ["Поле workflow %1 должно содержать целое число; получено: %2.", "Workflow field %1 expects an integer; received: %2."],
		workflow_field_number_expected: ["Поле workflow %1 должно содержать число; получено: %2.", "Workflow field %1 expects a number; received: %2."],
		workflow_field_finite_expected: ["Поле workflow %1 должно содержать конечное число; получено: %2.", "Workflow field %1 expects a finite number; received: %2."],
		errWorkflowInvalid: ["Workflow не прошёл проверку. Откройте ⚙ или добавьте метки к названиям нод.", "Workflow validation failed. Open ⚙ or add tags to node titles."],
		generate: ["Генерировать", "Generate"], generationTimeout: ["Таймаут генерации, с:", "Generation timeout, s:"],
		historyCheckSelection: ["Проверить выделение", "Check selection"], historyPlaceResult: ["Вставить результат генерации", "Place generated result"],
		historyPrepareSelection: ["Подготовить выделение", "Prepare selection"], inpaintMask: ["Маска inpaint", "Inpaint mask"],
		imageInputRoles: ["Входы изображений", "Image inputs"], selectedInputRole: ["Назначение выбранной ноды", "Selected node role"],
		rolePhotoshop: ["Изображение Photoshop", "Photoshop image"], roleReference: ["Референс", "Reference"],
		roleWorkflowFile: ["Файл из workflow", "Workflow file"], roleEmpty: ["Пустое изображение", "Empty image"],
		workflowStoredFile: ["Файл в workflow: ", "Workflow file: "], fileNotSpecified: ["файл не указан", "file not specified"],
		maskUsedByWorkflow: ["используется в workflow", "used by workflow"], maskUnusedByWorkflow: ["не используется в workflow", "not used by workflow"],
		maskUsageHelp: ["Статус показывает, используется ли выход MASK этой ноды в workflow. Выбор пункта не изменяет соединения нод в ComfyUI.", "The status shows whether this node's MASK output is used by the workflow. Selecting an option does not change node connections in ComfyUI."],
		errMainImageRoleRequired: ["Назначьте одной ноде LoadImage роль «Изображение Photoshop».", "Assign the Photoshop image role to one LoadImage node."],
		errMainImageRoleConflict: ["Роль «Изображение Photoshop» назначена нескольким нодам, которые не образуют единую группу #PS-INPUT.", "The Photoshop image role is assigned to multiple nodes that do not form a single #PS-INPUT group."],
		errSelectedMaskDisconnected: ["Выбранная «Маска inpaint» не используется в workflow. Выберите вариант со статусом «используется в workflow» или исправьте соединения в ComfyUI.", "The selected Inpaint mask is not used by the workflow. Select an option marked “used by workflow” or fix the connections in ComfyUI."],
		errEmptyRoleUnsupported: ["роль «Пустое изображение» поддерживается только стандартной нодой LoadImage", "the Empty image role is supported only for a standard LoadImage node"],
		imageReference: ["Референс", "Reference image"],
		noneReference: ["нет", "none"], selectReferenceImage: ["Выберите референсное изображение", "Select reference image"],
		errReferenceImageFormat: ["Поддерживаются только изображения JPG, JPEG, PNG и WebP.", "Only JPG, JPEG, PNG and WebP images are supported."],
		saveChanges: ["Сохранить изменения", "Save changes"], dialogYes: ["Да", "Yes"], dialogNo: ["Нет", "No"],
		dialogOk: ["ОК", "OK"], dialogCancel: ["Отмена", "Cancel"],
		saveWorkflowJson: ["Сохранить workflow как…", "Save workflow as…"],
		saveWorkflowAsPrompt: ["Сохранить workflow JSON как…", "Save workflow JSON as…"],
		saveForgeSchemaJson: ["Сохранить схему Forge как…", "Save Forge schema as…"],
		saveForgeSchemaAsPrompt: ["Сохранить JSON-схему Forge как…", "Save Forge schema JSON as…"],
		errSaveAsJsonExtension: ["Файл должен быть сохранён с расширением .json.", "The file must be saved with a .json extension."],
		infoEmptyWorkflowFolder: ["В выбранной папке нет API-workflow (*.json). Откройте настройки, чтобы выбрать другую папку.", "The selected folder contains no API workflows (*.json). Open Settings to choose another folder."],
		infoMissingWorkflowFolder: ["Папка API-workflow ComfyUI не выбрана или не найдена. Нажмите ⚙ и выберите папку с API-workflow.", "The ComfyUI API-workflow folder is not selected or cannot be found. Click ⚙ and select the API-workflow folder."],
		jsxLine: ["Строка JSX: ", "JSX line: "], layerMetadata: ["Сохранять настройки в метаданных слоя", "Store settings in layer metadata"],
		loadLayerMetadata: ["Загрузить параметры из метаданных активного слоя", "Load settings from active layer metadata"],
		errLayerMetadata: ["Не удалось сопоставить workflow из метаданных слоя.", "Could not match the workflow stored in layer metadata."], maximumMp: ["Макс. МП:", "Max MP:"],
		minimumSide: ["Мин. сторона:", "Min. side:"], resizePreset: ["Профиль автомасштаба", "Auto-resize profile"],
		resizePresetManagement: ["Профили автомасштаба", "Auto-resize profiles"], resizePresetNew: ["Новый профиль", "New profile"],
		resizePresetTitle: ["Профиль автомасштаба", "Auto-resize profile"], resizePresetPrompt: ["Укажите имя профиля автомасштаба", "Enter an auto-resize profile name"],
		resizeMinShort: ["мин", "min"], resizeMaxShort: ["макс", "max"], presetCopy: [" копия", " copy"],
		errResizePreset: ["Профиль «%1» уже существует. Перезаписать?", "Profile “%1” already exists. Overwrite?"], selectLora: ["Выберите LoRA", "Select LoRA"],
		loraSearch: ["Фильтр списка LoRA", "Filter the LoRA list"], selectModules: ["Выбрать VAE / Text Encoder", "Select VAE / Text Encoder"],
		modulesSearch: ["Фильтр списка VAE / Text Encoder", "Filter the VAE / Text Encoder list"], modulesNone: ["ничего не выбрано", "nothing selected"],
		lorasNone: ["ничего не выбрано", "nothing selected"], nodeInput: ["Нода #", "Node #"], none: ["Снять все", "Select none"],
		forgeDefaultLorasMissing: ["Некоторые LoRA, заданные по умолчанию в выбранной схеме Forge, не найдены и были пропущены: ", "Some default LoRAs from the selected Forge schema were not found and were skipped: "],
		forgeSavedLorasMissing: ["Некоторые сохранённые LoRA выбранной схемы Forge не найдены и были пропущены: ", "Some saved LoRAs for the selected Forge schema were not found and were skipped: "],
		opacity: ["Непрозрачность кисти", "Brush opacity"], imageSettings: ["Параметры изображения", "Image settings"], outputImage: ["Выходное изображение", "Output image"],
		comfyPort: ["Порт ComfyUI:", "ComfyUI port:"], presetNew: ["Новый пресет", "New preset"],
		errDefaultPreset: ["Используйте другое имя для пресета.", "Use a different preset name."],
		errPreset: ["Пресет «%1» уже существует. Перезаписать?", "Preset “%1” already exists. Overwrite?"], presetAdd: ["Добавить пресет", "Add preset"],
		presetDefault: ["по умолчанию", "default"], presetDelete: ["Удалить пресет", "Delete preset"], presetDeleteConfirmA: ["Удалить пресет «", "Delete preset ‘"],
		presetDeleteConfirmB: ["»?", "’?"], presetNamePrompt: ["Укажите имя пресета", "Enter preset name"],
		presetRestore: ["Восстановить значения пресета", "Restore preset values"], presetSave: ["Сохранить пресет", "Save preset"],
		translate: ["Перевести", "Translate"], translatePromptHelp: ["Перевести текущий промпт на английский", "Translate the current prompt into English"],
		progressTranslate: ["Перевод промпта", "Translating prompt"], errTranslate: ["Не удалось перевести промпт.", "Could not translate the prompt."],
		primarySize: ["Поля width / height", "Width / height fields"], sizeControlMode: ["Управление размером", "Size control"], sizeModeAuto: ["Автоматически", "Automatic"],
		sizeModeSourceImage: ["Размер входного изображения", "Input image size"], sizeModeBinding: ["Выбранные поля width / height", "Selected width / height fields"],
		sizeModeSourceHelp: ["Поля width/height workflow не изменяются. Размер задаётся экспортированным изображением Photoshop.", "Workflow width/height fields are not changed. The exported Photoshop image supplies the size."],
		sizeModeBindingHelp: ["Будет изменена выбранная пара полей:", "The selected field pair will be changed:"],
		sizeModeNoCandidates: ["В workflow не найдено ни одной безопасной пары width/height.", "No safe width/height pair was found in the workflow."],
		sizeModeAutoSelected: ["Автоматически выбрана пара:", "Automatically selected pair:"],
		sizeModeAutoFallback: ["Однозначная пара width/height не найдена. Будет использован размер входного изображения.", "No unambiguous width/height pair was found. The input image size will be used."],
		sizeModeAutoHelp: ["После сохранения анализатор выберет только однозначную безопасную пару width/height; иначе будет использован размер входного изображения.", "After saving, the analyzer will select only an unambiguous safe width/height pair; otherwise it will use the input image size."],
		errSizeBindingRequired: ["Выберите пару полей width/height или другой режим управления размером.", "Select a width/height field pair or another size-control mode."],
		generationProgressTitle: ["Генерация изображения", "Image generation"], progressAnalyze: ["Анализ workflow", "Analyzing workflow"],
		progressSaveJson: ["Сохранение значений в JSON…", "Saving values to JSON…"], progressGenerate: ["Генерация изображения… ", "Generating image… "],
		progressPrepare: ["Инициализация модели… ", "Initializing model… "], progressInitializeAction: ["инициализация", "initializing"],
		progressGenerateAction: ["генерация изображения", "generating image"], progressStartPython: ["Запуск Python-сервера…", "Starting Python server…"],
		progressInstallPython: ["Установка зависимостей Python…", "Installing Python dependencies…"],
		progressInitializing: ["Инициализация " + APP.name + "… ", "Initializing " + APP.name + "… "],
		progressHandshake: ["Подключение к Python API…", "Connecting to Python API…"], progressWorkflows: ["Загрузка списка workflow…", "Loading workflow list…"],
		progressReady: ["Подготовка интерфейса завершена", "Interface data is ready"], flatten: ["Объединять слои перед генерацией", "Flatten layers before generation"],
		keepAspectRatioDuringPlace: ["Сохранять пропорции при размещении", "Keep aspect ratio during place"],
		rasterize: ["Растеризовать сгенерированное изображение", "Rasterize generated image"], randomSeed: ["Установить случайный seed", "Set a random seed"],
		recommended: ["Рекомендуемые", "Recommended"], refreshWorkflows: ["Обновить список JSON", "Refresh JSON list"],
		rebuildWorkflow: ["Повторно проанализировать или полностью сбросить workflow", "Reanalyze or fully reset the workflow"],
		rebuildWorkflowConfirm: ["Выполнить полный сброс выбранного workflow?\n\nДа — удалить все данные этого workflow и проанализировать его заново.\nНет — только повторно проанализировать workflow, сохранив значения параметров, состав интерфейса, ручные привязки, настройки размера и reference images.", "Fully reset the selected workflow?\n\nYes — remove all data for this workflow and analyze it again.\nNo — only reanalyze the workflow while preserving parameter values, the interface layout, manual bindings, size settings and reference images."],
		selectBrush: ["Активировать кисть после генерации", "Select brush after generation"], selection: ["Выделение: ", "Selection: "],
		selectWorkflowFolder: ["Выберите папку с workflow, сохранёнными через Export Workflow (API)", "Select the folder containing workflows exported with Export Workflow (API)"],
		selectForgeSchemaFolder: ["Выберите папку с JSON-схемами Forge", "Select the folder containing Forge JSON schemas"],
		scriptSettings: ["Настройки скрипта", "Script settings"],
		sizeFromInput: ["В workflow нет width/height: итоговый размер задаётся загруженным JPEG.", "The workflow has no width/height: size is defined by the uploaded JPEG."],
		sizeMultiple: ["Кратность width/height:", "Width/height multiple:"],
		outputImageFormat: ["Формат результата:", "Output format:"],
		sizeWorkflowBinding: ["Размер будет записан в обнаруженные поля workflow.", "Size will be written to the detected workflow fields."], secondsShort: ["с", "s"],
		visibleParameters: ["Параметры главного окна", "Main-window parameters"], workflowFolder: ["Папка API-workflow:", "API workflow folder:"],
		forgeSchemaFolder: ["Папка схем Forge:", "Forge schema folder:"], workflowSettings: ["Настройки workflow", "Workflow settings"],
		forgeSchemaSettings: ["Настройки схемы Forge", "Forge schema settings"],
		forgeSchemaSettingsNote: ["Поля сгруппированы по назначению. Скрытые поля используют значения по умолчанию из JSON-схемы. Модель и VAE / Text encoders всегда видимы.", "Fields are grouped by purpose. Hidden fields use defaults from the JSON schema. Model and VAE / Text encoders are always visible."],
		alwaysVisible: ["всегда видно", "always visible"], backendLabel: ["Бэкенд", "Backend"], host: ["IP / хост:", "IP / host:"],
		schemaDefaultValue: ["Значение схемы: ", "Schema default: "],
		forgeGroupModel: ["Модель", "Model"], forgeGroupPrompt: ["Промпты", "Prompts"], forgeGroupSampling: ["Сэмплинг", "Sampling"],
		forgeGroupImage: ["Изображение", "Image"], forgeGroupAdvanced: ["Дополнительно", "Advanced"],
		workflowDiagnostics: ["Проверка workflow", "Workflow validation"], generationDiagnostics: ["Результат генерации", "Generation result"],
		diagnosticsErrors: ["Необходимо исправить:", "Must be fixed:"], diagnosticsWarnings: ["Обратите внимание:", "Please note:"],
		diagnosticsNeedAttention: ["Обнаружены проблемы", "Problems found"], diagnosticsInformation: ["Информация", "Information"],
		missing_node_class: ["В ComfyUI не установлена нода: %1", "The ComfyUI node is not installed: %1"],
		sampler_inputs_ambiguous: ["В сэмплере #%1 найдено несколько входов типа %2: %3. В качестве основного используется %4; остальные доступны как дополнительные поля.", "Sampler #%1 contains several inputs matching %2: %3. %4 is used as the standard control; the others remain available as advanced fields."],
		prompt_fields_ambiguous: ["Перед сэмплером #%2 найдено несколько равнозначных полей %1. Использовано первое найденное поле; чтобы выбрать другое, переименуйте нужную ноду или добавьте метку.", "Several equivalent %1 fields were found upstream of sampler #%2. The first deterministic match is used; rename or tag the intended node to make the workflow unambiguous."],
		selected_input_missing: ["Нода с ранее выбранной ролью «Изображение Photoshop» больше не существует. Откройте настройки workflow и назначьте эту роль заново.", "The node previously assigned the Photoshop image role no longer exists. Open Workflow settings and assign the role again."],
		selected_mask_missing: ["Ранее выбранный вариант параметра «Маска inpaint» больше не существует. Откройте настройки workflow и выберите его заново.", "The previously selected Inpaint mask option no longer exists. Open Workflow settings and select it again."],
		selected_references_missing: ["Некоторые ноды с ролью «Референс» больше не существуют и были пропущены: %1", "Some nodes assigned the Reference role no longer exist and were ignored: %1"],
		empty_role_unsupported: ["%1: роль «Пустое изображение» поддерживается только стандартной нодой LoadImage.", "%1 cannot use the empty-image role because it is not a standard LoadImage node."],
		image_role_conflict: ["%1 имеет конфликтующие назначения. Выберите для ноды только одну роль в настройках workflow.", "%1 has conflicting image roles. Choose only one role in workflow settings."],
		empty_roles_missing: ["Некоторые ноды с ролью «Пустое изображение» больше не существуют и были пропущены: %1", "Some LoadImage empty roles no longer exist and were ignored: %1"],
		selected_output_missing: ["Ранее выбранное «Выходное изображение» больше не существует. Откройте настройки workflow и выберите его заново.", "The previously selected Output image no longer exists. Open Workflow settings and select it again."],
		output_auto_selected: ["Найдено несколько выходных нод. В качестве «Выходного изображения» автоматически выбрана нода %1.", "Several output nodes were found. Node %1 was automatically selected as Output image."],
		size_binding_required: ["Для параметра «Управление размером» выбраны «Поля width / height», но сама пара полей не указана.", "Width / height fields is selected under Size control, but no field pair is specified."],
		selected_size_missing: ["Ранее выбранная пара «Поля width / height» больше не существует. Откройте настройки workflow и выберите её заново.", "The previously selected Width / height fields pair no longer exists. Open Workflow settings and select it again."],
		size_ambiguous: ["Найдено несколько вариантов для параметра «Поля width / height». При автоматическом «Управлении размером» будет использован «Размер входного изображения». Если workflow требует изменения конкретных полей, выберите пару вручную.", "Several options were found for Width / height fields. Automatic Size control will use Input image size. If the workflow requires specific fields to be changed, select a pair manually."],
		sampler_ambiguous: ["Найдено несколько равнозначных сэмплеров. Основным назначен первый найденный. Чтобы выбрать другой, добавьте к названию нужного сэмплера #PS-MAIN.", "Several equivalent sampler nodes were found. The first deterministic node is treated as primary. Add #PS-MAIN to the intended sampler title to make the choice explicit."],
		input_not_found: ["Не найдена нода, которой можно назначить роль «Изображение Photoshop». Добавьте #PS-INPUT к названию нужной ноды.", "No node was found that can use the Photoshop image role. Add #PS-INPUT to the required node title."],
		output_not_found: ["Не найден вариант для параметра «Выходное изображение». Добавьте #PS-OUTPUT к названию нужной ноды Save Image или Preview Image.", "No option was found for Output image. Add #PS-OUTPUT to the required Save Image or Preview Image node title."],
		size_not_found: ["Не найден вариант для параметра «Поля width / height». Скрипт отправит изображение Photoshop правильного размера, но итоговый размер зависит от логики workflow.", "No option was found for Width / height fields. The script will send a correctly sized Photoshop image, but the final size depends on the workflow logic."],
		sampler_not_found: ["Основной сэмплер не распознан; часть стандартных параметров может быть недоступна.", "The primary sampler was not recognized; standard parameters may be incomplete."],
		generation_reference_invalid: ["Референс №%1 не применён: повреждены сохранённые данные файла.", "Reference #%1 was not applied because its saved file data is invalid."],
		generation_reference_binding_missing: ["Референс №%1 не применён: не сохранено назначение ноде с ролью «Референс».", "Reference #%1 was not applied because no node assigned the Reference role was saved for it."],
		generation_reference_input_missing: ["Референс %1 не применён: соответствующая нода с ролью «Референс» отсутствует в текущей схеме. Повторно проанализируйте workflow.", "Reference %1 was not applied because its node assigned the Reference role is missing from the current schema. Reanalyze the workflow."],
		generation_reference_file_missing: ["Референс %1 не применён: файл не найден (%2).", "Reference %1 was not applied because the file was not found (%2)."],
		generation_size_binding_skipped: ["Автоматическая запись размера в «Поля width / height» пропущена: выбранная пара не принимает размер изображения Photoshop. Использован «Размер входного изображения».", "Automatic writing to Width / height fields was skipped because the selected pair cannot accept the Photoshop image size. Input image size was used instead."],
		generation_parameter_missing: ["Параметр %1 не применён: он отсутствует в текущей схеме. Повторно проанализируйте workflow.", "Parameter %1 was not applied because it is missing from the current schema. Reanalyze the workflow."],
		generation_parameter_no_targets: ["Параметр %1 не применён: в схеме для него нет целевых полей. Повторно проанализируйте workflow.", "Parameter %1 was not applied because the schema contains no target fields for it. Reanalyze the workflow."],
		forgePort: ["Порт Forge Neo:", "Forge Neo port:"], refreshForgeCatalog: ["Обновить список схем и данные текущей схемы", "Refresh schema list and current schema data"],
		rebuildForgeSchema: ["Повторно загрузить или полностью сбросить схему Forge", "Reload or fully reset the Forge schema"],
		rebuildForgeSchemaConfirm: ["Выполнить полный сброс выбранной схемы Forge?\n\nДа — удалить все данные этой схемы и заново загрузить её из JSON.\nНет — только повторно загрузить схему из JSON, сохранив значения параметров, состав интерфейса, выбранные LoRA, ImageStitch inputs и настройки размера.", "Fully reset the selected Forge schema?\n\nYes — remove all data for this schema and load it again from JSON.\nNo — only reload the schema from JSON while preserving parameter values, the interface layout, selected LoRAs, ImageStitch inputs and size settings."],
		progressForgeCatalog: ["Загрузка данных Forge Neo…", "Loading Forge Neo data…"], progressForgePresets: ["Загрузка схем Forge…", "Loading Forge schemas…"],
		infoEmptyForgePresets: ["В выбранной папке нет подходящих JSON-схем Forge. Откройте настройки, чтобы выбрать другую папку.", "The selected folder contains no compatible Forge JSON schemas. Open Settings to choose another folder."],
		infoMissingForgeSchemaFolder: ["Папка JSON-схем Forge не выбрана или не найдена. Нажмите ⚙ и выберите папку со схемами.", "The Forge JSON schema folder is not selected or cannot be found. Click ⚙ and select the schema folder."],
		detectedBackends: ["Доступные бэкенды:", "Available backends:"], detectBackends: ["Найти запущенные бэкенды", "Detect running backends"],
		backendsNone: ["не найдены", "none detected"],
		errNoBackendAvailable: ["Не найден запущенный ComfyUI или Forge Neo. Запустите хотя бы одну оболочку и повторите запуск скрипта.", "No running ComfyUI or Forge Neo instance was detected. Start at least one backend and run the script again."],
		errBackendUnavailable: ["Выбранный бэкенд сейчас недоступен.", "The selected backend is currently unavailable."],
		workflowTagNote: ["Метки можно дописать прямо к заголовкам нод в ComfyUI: #PS-INPUT, #PS-OUTPUT, #PS-SIZE, #PS-MAIN, #PS-REF, #PS-MASK и #PS-UI. После переименования снова выполните Export Workflow (API). Ручное редактирование JSON не требуется.", "Append tags directly to node titles in ComfyUI: #PS-INPUT, #PS-OUTPUT, #PS-SIZE, #PS-MAIN, #PS-REF, #PS-MASK and #PS-UI. Export Workflow (API) again after renaming. Manual JSON editing is not required."]
	},
		plain = {
		errPythonStartB: ".", cfgScale: "CFG Scale", guidance: "Guidance", imageStitchInputs: "ImageStitch inputs", imageStitchInput: "ImageStitch input",
		negativePrompt: "Negative prompt", lora: "LoRA", presetRefreshButton: "↻", presetAddButton: "+", presetSaveButton: "✔", presetDeleteButton: "×", prompt: "Prompt",
		sampler: "Sampling method", scheduler: "Schedule type", seed: "Seed", resize: "Resize", steps: "Sampling steps", denoisingStrength: "Denoising strength",
		workflow: "Workflow", uiPreset: "UI Preset", modules: "VAE / Text encoders", distilledCfgScale: "Distilled CFG Scale", shift: "Shift"
		},
		key;
	for (key in localized) if (localized.hasOwnProperty(key))
		this[key] = { ru: localized[key][0], en: localized[key][1] };
	for (key in plain) if (plain.hasOwnProperty(key)) this[key] = plain[key];
}
// ---
// РАЗМЕРЫ, PRESETS И ОБЩИЕ ЧИСТЫЕ HELPERS
// ---
function resolveProfileSizeMultiple(schema, profile) {
	var fallback = clamp(parseInt(cfg.sizeMultiple, 10) || 16, 1, 256),
		profileValue = profile ? profile.sizeMultiple : null,
		parsed;
	if (schema && backend.schemaBackend(schema) == BACKEND_FORGE) {
		if (profileValue !== null && profileValue !== undefined && profileValue !== "") {
			parsed = parseInt(profileValue, 10);
			if (!isNaN(parsed)) return clamp(parsed, 1, 256);
		}
		parsed = parseInt(schema.size_multiple, 10);
		return isNaN(parsed) ? fallback : clamp(parsed, 1, 256);
	}
	parsed = parseInt(profileValue, 10);
	return isNaN(parsed) ? fallback : clamp(parsed, 1, 256);
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
	this.defaultPrompt = function () { return cloneObj(promptDefaults); };
	this.promptStore = function (config, context) {
		context = context == "negative" ? "negative" : "positive";
		if (!isObjectMap(config.promptPresets))
			config.promptPresets = config.data.promptPresets = self.defaultPrompt();
		if (!isObjectMap(config.promptPresets[context]))
			config.promptPresets[context] = {};
		config.data.promptPresets = config.promptPresets;
		return config.promptPresets[context];
	};
	this.promptText = function (text) { return String(text || ""); };
	this.applyPrompt = function (presetText) { return String(presetText || ""); };
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
		return preset.name + " (" + str.resizeMinShort + " " + preset.minSide + " px, " + str.resizeMaxShort + " " + preset.maxMp + " MP)";
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
function toBooleanValue(value) {
	if (typeof value == "string")
		return /^(1|true|yes|on)$/i.test(value.replace(/^\s+|\s+$/g, ""));
	return !!value;
}
function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
function roundTo(value, digits) { var k = Math.pow(10, digits || 0); return Math.round(value * k) / k; }
// Единая дискретизация ScriptUI Slider.
// Drag привязывается к сетке step, а клавиша/колесо вне drag всегда дают
// ровно один логический шаг независимо от собственного native шага ScriptUI.
function createSliderStepper(slider, step, origin) {
	step = Math.abs(Number(step));
	if (!isFinite(step) || step <= 0) step = 1;
	origin = Number(origin);
	if (!isFinite(origin)) origin = Number(slider.minvalue) || 0;
	var state = {
		slider: slider,
		step: step,
		origin: origin,
		snappedValue: null,
		pointerActive: false
	};
	try { slider.addEventListener("mousedown", function () { state.pointerActive = true; }); } catch (_) { }
	state.sync = function (reset) {
		var raw = Number(slider.value),
			previous = reset ? null : state.snappedValue,
			value;
		if (!state.pointerActive && previous !== null && raw != previous)
			value = roundByStep(previous + (raw > previous ? step : -step), step, origin);
		else value = roundByStep(raw, step, origin);
		value = clamp(value, Number(slider.minvalue), Number(slider.maxvalue));
		slider.value = value;
		state.snappedValue = value;
		return value;
	};
	state.reset = function (value) {
		if (value !== undefined) slider.value = value;
		return state.sync(true);
	};
	state.finish = function () {
		var value = state.sync(false);
		state.pointerActive = false;
		return value;
	};
	state.sync(true);
	return state;
}
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
function normalizeOutputFormat(value) {
	var format = String(value || "").toLowerCase();
	if (format == "jpeg") format = "jpg";
	return format == "png" ? "png" : "jpg";
}
function arrayContains(array, value) { if (!array) return false; for (var i = 0; i < array.length; i++) if (array[i] == value) return true; return false; }
function normalizedBindingOverrides(value) {
	value = isObjectMap(value) ? value : {};
	var references = value.references instanceof Array ? value.references.slice(0) : [],
		emptyInputs = value.emptyInputs instanceof Array ? value.emptyInputs.slice(0) : [], i;
	for (i = 0; i < references.length; i++) references[i] = String(references[i] || "");
	for (i = 0; i < emptyInputs.length; i++) emptyInputs[i] = String(emptyInputs[i] || "");
	references.sort();
	emptyInputs.sort();
	return {
		input: String(value.input || ""),
		mask: String(value.mask || ""),
		references: references,
		referencesConfigured: value.referencesConfigured === true,
		emptyInputs: emptyInputs,
		output: String(value.output || ""),
		sizeMode: value.sizeMode == "source_image" || value.sizeMode == "binding" ? String(value.sizeMode) : "auto",
		size: value.sizeMode == "binding" ? String(value.size || "") : ""
	};
}
function bindingOverridesEqual(first, second) {
	return jsonStringify(normalizedBindingOverrides(first)) == jsonStringify(normalizedBindingOverrides(second));
}
function resolveForgeVisibleControls(schema, profile) {
	var recommended = schema && schema.recommended_controls instanceof Array
		? schema.recommended_controls
		: [],
		controls = schema && schema.controls instanceof Array ? schema.controls : [],
		values = profile && isObjectMap(profile.values) ? profile.values : {},
		visible = profile && profile.visibleControls instanceof Array
			? cloneObj(profile.visibleControls)
			: cloneObj(recommended),
		i, id, control;
	for (i = 0; i < controls.length; i++) {
		control = controls[i];
		id = String(control && control.id || "");
		if (!id || arrayContains(visible, id)) continue;
		if (control.required_visible ||
			(arrayContains(recommended, id) && !values.hasOwnProperty(id)))
			visible.push(id);
	}
	if (schema && schema.capabilities && schema.capabilities.image_stitch &&
		arrayContains(recommended, "image_stitch") &&
		!arrayContains(visible, "image_stitch") &&
		!values.hasOwnProperty("image_stitch"))
		visible.push("image_stitch");
	if (profile) profile.visibleControls = cloneObj(visible);
	return visible;
}
function isObjectMap(value) { return !!value && typeof value == "object" && !(value instanceof Array); }
function startsWithSemantic(controlId, semantic) { return controlId == semantic || controlId.indexOf(semantic + "__") === 0; }
function cloneObj(source) {
	if (source === null || source === undefined || typeof source != "object") return source;
	var res = source instanceof Array ? [] : {}, key;
	for (key in source) if (source.hasOwnProperty(key)) res[key] = cloneObj(source[key]);
	return res;
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
