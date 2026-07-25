let schema = { web: {}, desktop: {} };

let state = {
  backend: "web",
  steps: [],
  selected: null, // the model step whose parameters the properties panel shows
  variables: {}, // declared workflow variables: name -> default value (see resolve_sub_workflows/Workflow.variables)
};

let currentJobId = null;
let currentRecordId = null;
let recordingSource = null;

const el = (id) => document.getElementById(id);

// --- icons: one consistent, monochrome SVG set (currentColor) instead of
// mixed emoji, so icon-only controls render identically across platforms ---
function icon(inner) {
  return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${inner}</svg>`;
}
const ICONS = {
  clipboard: icon('<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1"/><line x1="9" y1="11" x2="15" y2="11"/><line x1="9" y1="15" x2="15" y2="15"/>'),
  target: icon('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>'),
  keyboard: icon('<rect x="3" y="6" width="18" height="12" rx="2"/><line x1="7" y1="10" x2="7" y2="10"/><line x1="10" y1="10" x2="10" y2="10"/><line x1="13" y1="10" x2="13" y2="10"/><line x1="16" y1="10" x2="16" y2="10"/><line x1="8" y1="14" x2="15" y2="14"/>'),
  dot: icon('<circle cx="12" cy="12" r="6" fill="currentColor" stroke="none"/>'),
  stop: icon('<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none"/>'),
  monitor: icon('<rect x="3" y="4" width="18" height="12" rx="2"/><line x1="8" y1="20" x2="16" y2="20"/><line x1="12" y1="16" x2="12" y2="20"/>'),
  pause: icon('<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>'),
  play: icon('<path d="M7 4.5v15l13-7.5z" fill="currentColor" stroke="none"/>'),
  grip: icon('<circle cx="9" cy="6" r="1.3" fill="currentColor" stroke="none"/><circle cx="9" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="9" cy="18" r="1.3" fill="currentColor" stroke="none"/><circle cx="15" cy="6" r="1.3" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="15" cy="18" r="1.3" fill="currentColor" stroke="none"/>'),
  pencil: icon('<path d="M4 20l1-4 11-11 3 3-11 11-4 1z"/><path d="M13.5 6.5l4 4"/>'),
  copy: icon('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>'),
  trash: icon('<path d="M4 7h16"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>'),
  document: icon('<path d="M7 3h7l4 4v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v4h4"/>'),
  check: icon('<polyline points="5 13 9 17 19 7"/>'),
};

// --- toasts + confirm modal: replace native alert()/confirm(), which break out
// of the app's design language with an unstyled browser dialog ---
function toast(message, type = "info") {
  const host = el("toast-host");
  const node = document.createElement("div");
  node.className = "toast" + (type === "error" ? " toast-error" : type === "success" ? " toast-success" : "");
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => node.remove(), 4000);
}

function confirmDialog(message, confirmLabel = "Löschen") {
  return new Promise((resolve) => {
    const overlay = el("confirm-overlay");
    const okBtn = el("confirm-ok");
    const cancelBtn = el("confirm-cancel");
    el("confirm-message").textContent = message;
    okBtn.textContent = confirmLabel;
    overlay.classList.remove("hidden");

    const cleanup = (result) => {
      overlay.classList.add("hidden");
      okBtn.removeEventListener("click", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      overlay.removeEventListener("click", onOverlay);
      document.removeEventListener("keydown", onKey);
      resolve(result);
    };
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onOverlay = (e) => {
      if (e.target === overlay) cleanup(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") cleanup(false);
    };
    okBtn.addEventListener("click", onOk);
    cancelBtn.addEventListener("click", onCancel);
    overlay.addEventListener("click", onOverlay);
    document.addEventListener("keydown", onKey);
  });
}

function newStepFor(backend) {
  const actions = schema[backend] || {};
  return makeStep(Object.keys(actions)[0], actions);
}

// --- undo ---

let undoStack = [];
const MAX_UNDO = 50;

function snapshotState() {
  return JSON.stringify({
    name: el("wf-name").value,
    backend: state.backend,
    browserChannel: el("wf-browser-channel").value,
    steps: state.steps,
    variables: state.variables,
  });
}

function pushUndo() {
  undoStack.push(snapshotState());
  if (undoStack.length > MAX_UNDO) undoStack.shift();
  updateUndoButton();
}

function undo() {
  if (undoStack.length === 0) return;
  const snap = JSON.parse(undoStack.pop());
  el("wf-name").value = snap.name;
  state.backend = snap.backend;
  el("wf-backend").value = snap.backend;
  el("wf-browser-channel").value = snap.browserChannel || "";
  updateBrowserChannelVisibility();
  state.steps = snap.steps;
  state.variables = snap.variables || {};
  refreshVariableNamesDatalist();
  state.selected = null;
  renderSteps();
  updateUndoButton();
}

function updateBrowserChannelVisibility() {
  el("wf-browser-channel").classList.toggle("hidden", state.backend !== "web");
}

function updateUndoButton() {
  el("btn-undo").disabled = undoStack.length === 0;
}

async function loadSchema() {
  const res = await fetch("/api/schema");
  schema = await res.json();
}

async function loadWorkflowList() {
  const res = await fetch("/api/workflows");
  const names = await res.json();
  const select = el("wf-load");
  select.innerHTML = '<option value="">Workflow laden...</option>';
  const datalist = el("workflow-names");
  datalist.innerHTML = "";
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
    // the same names feed every "workflow"-typed field, e.g. run_workflow's
    const suggestion = document.createElement("option");
    suggestion.value = name;
    datalist.appendChild(suggestion);
  }
}

async function loadWorkflow(name) {
  const res = await fetch(`/api/workflows/${encodeURIComponent(name)}`);
  if (!res.ok) return;
  const data = await res.json();
  el("wf-name").value = data.name;
  el("wf-backend").value = data.backend;
  state.backend = data.backend;
  el("wf-browser-channel").value = data.browser_channel || "";
  updateBrowserChannelVisibility();
  state.steps = rawStepsToModel(data.steps, schema[state.backend] || {});
  state.variables = data.variables || {};
  refreshVariableNamesDatalist();
  state.selected = null;
  undoStack = [];
  updateUndoButton();
  renderSteps();
}

function fieldValue(field, raw) {
  if (raw === "" || raw === undefined) return undefined;
  if (field.type === "number") return Number(raw);
  if (field.type === "checkbox") return !!raw;
  if (field.type === "json") {
    try {
      return JSON.parse(raw);
    } catch {
      return undefined; // invalid JSON while typing - don't commit yet, keep last valid value
    }
  }
  return raw;
}

function isScopeStep(step, index) {
  return state.backend === "desktop" && index === 0 && ["launch", "connect"].includes(step.action);
}

// --- model <-> wire format -------------------------------------------------
//
// The builder edits a "model" tree in which a control-flow branch is a real
// array (step.slots[].steps), while the wire format (workflows/*.yaml,
// engine.py's Step.from_dict) keeps branches inside the step's own params as
// flat {action, ...params} dicts. Decoding the *whole* tree up front - rather
// than decoding each branch lazily while rendering it, as this builder used to
// do - is what turns "drag an activity from one container into another" into a
// plain array move instead of a re-encode of both parents.
//
// A slot is one drop target: either a named branch field ({kind:"field"}, e.g.
// if.then, try.catch) or one case of a switch ({kind:"case"}, whose caseKey is
// the user-editable match value).

function slotFieldsFor(action, actions) {
  return (actions[action] || []).filter((f) => f.type === "steps" || f.type === "cases");
}

function emptySlotsFor(action, actions) {
  return slotFieldsFor(action, actions)
    .filter((field) => field.type === "steps")
    .map((field) => ({ kind: "field", name: field.name, label: field.label, steps: [] }));
}

function makeStep(action, actions) {
  return {
    action,
    params: {},
    breakpoint: false,
    save_as: "",
    on_error: "",
    retry_count: 3,
    retry_delay: 2,
    slots: emptySlotsFor(action, actions),
  };
}

function rawStepToModel(raw, actions) {
  const { action, breakpoint, save_as, on_error, retry_count, retry_delay, ...params } = raw;
  const slots = [];
  for (const field of slotFieldsFor(action, actions)) {
    if (field.type === "cases") {
      for (const [key, list] of Object.entries(params[field.name] || {})) {
        slots.push({ kind: "case", name: field.name, caseKey: key, label: key, steps: rawStepsToModel(list, actions) });
      }
    } else {
      slots.push({
        kind: "field",
        name: field.name,
        label: field.label,
        steps: rawStepsToModel(params[field.name], actions),
      });
    }
    delete params[field.name];
  }
  return {
    action,
    params,
    breakpoint: !!breakpoint,
    save_as: save_as || "",
    on_error: on_error || "",
    retry_count: retry_count ?? 3,
    retry_delay: retry_delay ?? 2,
    slots,
  };
}

function rawStepsToModel(rawList, actions) {
  return (rawList || []).map((raw) => rawStepToModel(raw, actions));
}

function modelStepToRaw(model) {
  const entry = { action: model.action, ...model.params };
  for (const slot of model.slots || []) {
    if (slot.kind === "case") {
      // An empty case is still a case the author declared - keep it, unlike an
      // empty branch field, which is simply absent from the YAML.
      entry[slot.name] = entry[slot.name] || {};
      entry[slot.name][slot.caseKey] = modelStepsToRaw(slot.steps);
    } else if (slot.steps.length) {
      entry[slot.name] = modelStepsToRaw(slot.steps);
    }
  }
  if (model.breakpoint) entry.breakpoint = true;
  if (model.save_as) entry.save_as = model.save_as;
  if (model.on_error) {
    entry.on_error = model.on_error;
    if (model.on_error === "retry") {
      entry.retry_count = model.retry_count ?? 3;
      entry.retry_delay = model.retry_delay ?? 2;
    }
  }
  return entry;
}

function modelStepsToRaw(modelList) {
  return (modelList || []).map(modelStepToRaw);
}

// --- activity catalog ------------------------------------------------------

let catalog = { categories: [], activities: {} };
let catalogIndex = new Map(); // "backend/action" -> catalog entry

async function loadCatalog() {
  catalog = await (await fetch("/api/activities")).json();
  catalogIndex = new Map();
  for (const [backend, entries] of Object.entries(catalog.activities || {})) {
    for (const entry of entries) catalogIndex.set(`${backend}/${entry.name}`, entry);
  }
}

function activityMeta(action) {
  return (
    catalogIndex.get(`${state.backend}/${action}`) || { name: action, label: action, description: "", category: "Weitere" }
  );
}

function matchesQuery(entry, query) {
  if (!query) return true;
  const haystack = [entry.name, entry.label, entry.description, entry.category, ...(entry.keywords || [])]
    .join(" ")
    .toLowerCase();
  // every term must appear somewhere, so "excel lesen" narrows rather than widens
  return query.split(/\s+/).every((term) => haystack.includes(term));
}

function renderCatalog() {
  const host = el("catalog-list");
  host.innerHTML = "";
  const query = el("catalog-search").value.trim().toLowerCase();
  const entries = (catalog.activities[state.backend] || []).filter((entry) => matchesQuery(entry, query));

  if (entries.length === 0) {
    const empty = document.createElement("p");
    empty.className = "catalog-empty";
    empty.textContent = "Keine Aktivität gefunden.";
    host.appendChild(empty);
    return;
  }

  const known = catalog.categories || [];
  const categories = [...known, ...new Set(entries.map((e) => e.category))].filter(
    (name, i, all) => all.indexOf(name) === i
  );

  for (const category of categories) {
    const inCategory = entries.filter((entry) => entry.category === category);
    if (inCategory.length === 0) continue;

    const heading = document.createElement("div");
    heading.className = "catalog-category";
    heading.textContent = category;
    host.appendChild(heading);

    for (const entry of inCategory) {
      const item = document.createElement("div");
      item.className = "catalog-item";
      item.dataset.action = entry.name;
      item.title = `${entry.description}\n\nZiehen oder klicken zum Anhängen`;
      item.setAttribute("role", "button");
      item.tabIndex = 0;

      const label = document.createElement("span");
      label.className = "catalog-item-label";
      label.textContent = entry.label;

      const desc = document.createElement("span");
      desc.className = "catalog-item-desc";
      desc.textContent = entry.description;

      item.append(label, desc);
      // Clicking appends to the end of the sequence - the keyboard-reachable
      // path to the same result as dragging, which a pointer-only affordance
      // would leave without one.
      const append = () => appendActivity(entry.name);
      item.addEventListener("click", append);
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          append();
        }
      });
      host.appendChild(item);
    }
  }
}

function appendActivity(action) {
  const actions = schema[state.backend] || {};
  pushUndo();
  const step = makeStep(action, actions);
  state.steps.push(step);
  state.selected = step;
  renderSteps();
}

// --- canvas ----------------------------------------------------------------

// Maps a rendered list element back to the model array it shows, so a drop can
// be applied as a splice without walking the tree to find out where it landed.
let listRegistry = new Map();
let listCounter = 0;
// Step-list instances are torn down and rebuilt with every render; the catalog
// is rendered independently of the canvas, so its instance is created once and
// deliberately kept out of that cycle.
let sortables = [];
let catalogSortable = null;

function registerList(element, stepsArray) {
  const id = `sl-${listCounter++}`;
  element.dataset.listId = id;
  listRegistry.set(id, stepsArray);
  return element;
}

function destroySortables() {
  for (const instance of sortables) instance.destroy();
  sortables = [];
}

function stepSummary(step) {
  // `primary` is an ordered list of candidate fields, since the same action can
  // carry different ones per backend (see ACTION_META in schema.py).
  for (const name of activityMeta(step.action).primary || []) {
    const value = step.params[name];
    if (value !== undefined && value !== "" && value !== null) return String(value);
  }
  const first = Object.entries(step.params).find(([, v]) => v !== "" && v !== undefined && v !== null);
  return first ? `${first[0]}: ${first[1]}` : "";
}

function selectStep(step) {
  state.selected = step;
  for (const card of document.querySelectorAll(".step-card")) card.classList.remove("selected");
  const active = document.querySelector(`.step-card[data-step-path="${cssEscape(step.__path)}"]`);
  if (active) active.classList.add("selected");
  renderProperties();
}

function cssEscape(value) {
  return window.CSS && CSS.escape ? CSS.escape(value) : String(value);
}

function deleteStep(list, index) {
  pushUndo();
  const [removed] = list.splice(index, 1);
  if (removed === state.selected) state.selected = null;
  renderSteps();
}

function renderStepCard(step, index, actions, opts) {
  const card = document.createElement("div");
  card.className = "step-card" + (opts.isScope ? " scope-card" : "") + (step === state.selected ? " selected" : "");
  // Structural address, matching the `path` the engine reports when it pauses
  // (see engine.py's _run_steps) - this is what the breakpoint highlight and
  // the selection lookup key off.
  card.dataset.stepPath = opts.path;
  step.__path = opts.path;

  const head = document.createElement("div");
  head.className = "step-card-head";

  if (!opts.isScope) {
    const handle = document.createElement("span");
    handle.className = "drag-handle";
    handle.title = "Ziehen zum Verschieben";
    handle.innerHTML = ICONS.grip;
    head.appendChild(handle);
  }

  const bpToggle = document.createElement("button");
  bpToggle.type = "button";
  bpToggle.className = "bp-toggle" + (step.breakpoint ? " active" : "");
  bpToggle.title = step.breakpoint ? "Haltepunkt entfernen" : "Haltepunkt setzen";
  bpToggle.setAttribute("aria-label", bpToggle.title);
  bpToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    pushUndo();
    step.breakpoint = !step.breakpoint;
    renderSteps();
  });
  head.appendChild(bpToggle);

  const idx = document.createElement("span");
  idx.className = "step-index";
  idx.textContent = index + 1;
  head.appendChild(idx);

  const title = document.createElement("span");
  title.className = "step-title";
  title.textContent = activityMeta(step.action).label;
  head.appendChild(title);

  if (opts.isScope) {
    // Says why this card has no handle and no delete button: everything below
    // it runs against the application it opens.
    const badge = document.createElement("span");
    badge.className = "scope-badge";
    badge.innerHTML = ICONS.monitor + "<span>Anwendungs-Scope</span>";
    head.appendChild(badge);
  }

  const summary = document.createElement("span");
  summary.className = "step-summary";
  summary.textContent = stepSummary(step);
  head.appendChild(summary);

  if (!opts.isScope) {
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-icon danger step-delete";
    delBtn.innerHTML = ICONS.trash;
    delBtn.title = "Aktivität löschen";
    delBtn.setAttribute("aria-label", "Aktivität löschen");
    delBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteStep(opts.list, index);
    });
    head.appendChild(delBtn);
  }

  card.appendChild(head);
  card.addEventListener("click", (e) => {
    e.stopPropagation();
    selectStep(step);
  });

  for (const slot of step.slots || []) {
    card.appendChild(renderSlot(step, slot, actions, opts.path));
  }

  if (slotFieldsFor(step.action, actions).some((f) => f.type === "cases")) {
    const addCase = document.createElement("button");
    addCase.type = "button";
    addCase.className = "btn btn-add-case";
    addCase.textContent = "+ Fall";
    addCase.addEventListener("click", (e) => {
      e.stopPropagation();
      pushUndo();
      const field = slotFieldsFor(step.action, actions).find((f) => f.type === "cases");
      const defaultSlotAt = step.slots.findIndex((s) => s.kind === "field" && s.name === "default");
      const newSlot = { kind: "case", name: field.name, caseKey: "", label: "", steps: [] };
      // keep the "Standard-Fall" slot last, the way switch reads top to bottom
      if (defaultSlotAt === -1) step.slots.push(newSlot);
      else step.slots.splice(defaultSlotAt, 0, newSlot);
      renderSteps();
    });
    card.appendChild(addCase);
  }

  return card;
}

function renderSlot(parentStep, slot, actions, parentPath) {
  const wrap = document.createElement("div");
  wrap.className = "slot";

  const head = document.createElement("div");
  head.className = "slot-head";

  if (slot.kind === "case") {
    const keyInput = document.createElement("input");
    keyInput.type = "text";
    keyInput.className = "slot-case-key";
    keyInput.placeholder = "Wert (z.B. DE)";
    keyInput.value = slot.caseKey;
    keyInput.addEventListener("click", (e) => e.stopPropagation());
    keyInput.addEventListener("focus", () => pushUndo());
    keyInput.addEventListener("input", () => {
      slot.caseKey = keyInput.value;
      slot.label = keyInput.value;
    });
    // Re-render on blur rather than per keystroke: the key is part of every
    // nested step's path, so the paths are only rebuilt once the edit is done.
    keyInput.addEventListener("change", () => renderSteps());

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btn-icon danger";
    removeBtn.textContent = "✕";
    removeBtn.title = "Fall entfernen";
    removeBtn.setAttribute("aria-label", "Fall entfernen");
    removeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      pushUndo();
      parentStep.slots.splice(parentStep.slots.indexOf(slot), 1);
      renderSteps();
    });

    head.append(keyInput, removeBtn);
  } else {
    const label = document.createElement("span");
    label.className = "slot-label";
    label.textContent = slot.label;
    head.appendChild(label);
  }
  wrap.appendChild(head);

  const list = document.createElement("div");
  list.className = "step-list step-list-nested";
  registerList(list, slot.steps);
  const prefix = slot.kind === "case" ? `${parentPath}.${slot.name}.${slot.caseKey}` : `${parentPath}.${slot.name}`;
  slot.steps.forEach((child, i) => {
    list.appendChild(renderStepCard(child, i, actions, { path: `${prefix}.${i}`, list: slot.steps }));
  });
  wrap.appendChild(list);

  return wrap;
}

function renderRecordingHost() {
  const host = el("record-controls-host");
  host.innerHTML = "";
  if (state.steps.length && isScopeStep(state.steps[0], 0)) {
    host.appendChild(renderRecordingControls());
  }
}

function renderSteps() {
  const container = el("steps");
  container.innerHTML = "";
  listRegistry = new Map();
  listCounter = 0;
  destroySortables();

  const actions = schema[state.backend] || {};
  renderRecordingHost();

  const list = document.createElement("div");
  list.className = "step-list step-list-root";
  registerList(list, state.steps);
  state.steps.forEach((step, i) => {
    list.appendChild(
      renderStepCard(step, i, actions, { path: String(i), list: state.steps, isScope: isScopeStep(step, i) })
    );
  });
  container.appendChild(list);

  initSortables();
  renderProperties();
}

// --- drag & drop -----------------------------------------------------------

function rootListElement() {
  return document.querySelector(".step-list-root");
}

function hasScopeStep() {
  return state.steps.length > 0 && isScopeStep(state.steps[0], 0);
}

function onSortableMove(evt) {
  // A container may not be dropped into one of its own branches - that would
  // detach the subtree from the workflow and lose it.
  if (evt.dragged.contains(evt.to)) return false;
  // The desktop scope (launch/connect) has to stay the first activity, since
  // it is what every later step attaches to.
  if (evt.to === rootListElement() && hasScopeStep()) {
    if (evt.related && evt.related.classList.contains("scope-card") && !evt.willInsertAfter) return false;
  }
  return true;
}

function onSortableEnd(evt) {
  const targetList = listRegistry.get(evt.to.dataset.listId);
  if (!targetList) return;

  if (evt.from === el("catalog-list")) {
    // Dropped in from the palette: the clone Sortable left behind is thrown
    // away, the re-render below draws the real card.
    evt.item.remove();
    pushUndo();
    const step = makeStep(evt.item.dataset.action, schema[state.backend] || {});
    targetList.splice(evt.newIndex, 0, step);
    state.selected = step;
    renderSteps();
    return;
  }

  const sourceList = listRegistry.get(evt.from.dataset.listId);
  if (!sourceList) return;
  if (sourceList === targetList && evt.oldIndex === evt.newIndex) return;

  pushUndo();
  const [moved] = sourceList.splice(evt.oldIndex, 1);
  if (moved === undefined) {
    renderSteps();
    return;
  }
  // newIndex already refers to the post-move DOM position, which matches the
  // array position after the removal above - so it needs no adjustment.
  targetList.splice(evt.newIndex, 0, moved);
  renderSteps();
}

function initSortables() {
  const options = {
    group: { name: "uiflow-activities" },
    draggable: ".step-card",
    filter: ".scope-card",
    handle: ".drag-handle",
    animation: 150,
    forceFallback: true,
    fallbackOnBody: true,
    ghostClass: "drag-ghost",
    chosenClass: "drag-chosen",
    emptyInsertThreshold: 12,
    onMove: onSortableMove,
    onEnd: onSortableEnd,
  };
  for (const listEl of document.querySelectorAll("#steps .step-list")) {
    sortables.push(Sortable.create(listEl, options));
  }
}

function initCatalogSortable() {
  if (catalogSortable) catalogSortable.destroy();
  catalogSortable = Sortable.create(el("catalog-list"), {
    group: { name: "uiflow-activities", pull: "clone", put: false },
    sort: false,
    draggable: ".catalog-item",
    animation: 150,
    // Pointer-driven rather than the native HTML5 drag API: that is what makes
    // the drag work on touch devices and lets ghostClass/chosenClass style the
    // drag, instead of the browser's own un-styleable drag image.
    forceFallback: true,
    fallbackOnBody: true,
    onMove: onSortableMove,
    onEnd: onSortableEnd,
  });
}

// --- properties panel ------------------------------------------------------

function refreshSelectedSummary() {
  if (!state.selected) return;
  const card = document.querySelector(".step-card.selected .step-summary");
  if (card) card.textContent = stepSummary(state.selected);
}

function renderField(step, fieldDef) {
  const wrap = document.createElement("div");
  wrap.className = "field" + (fieldDef.type === "checkbox" ? " checkbox" : "");

  const label = document.createElement("label");
  label.textContent = fieldDef.label + (fieldDef.required ? " *" : "");

  let input;
  let editing = false;
  if (fieldDef.type === "select") {
    input = document.createElement("select");
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "(default)";
    input.appendChild(blank);
    for (const optValue of fieldDef.options || []) {
      const opt = document.createElement("option");
      opt.value = optValue;
      opt.textContent = optValue;
      input.appendChild(opt);
    }
    input.value = step.params[fieldDef.name] ?? "";
    input.addEventListener("change", () => pushUndo());
  } else if (fieldDef.type === "checkbox") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = !!step.params[fieldDef.name];
    input.addEventListener("change", () => pushUndo());
  } else if (fieldDef.type === "json") {
    input = document.createElement("textarea");
    input.rows = 5;
    input.value = JSON.stringify(step.params[fieldDef.name] ?? {}, null, 2);
    input.addEventListener("focus", () => {
      if (!editing) {
        pushUndo();
        editing = true;
      }
    });
    input.addEventListener("blur", () => {
      editing = false;
    });
  } else if (fieldDef.type === "hotkey") {
    input = document.createElement("input");
    input.type = "text";
    input.readOnly = true;
    input.placeholder = "z.B. ctrl+s";
    input.value = step.params[fieldDef.name] ?? "";
  } else if (fieldDef.type === "workflow") {
    // Free text with suggestions rather than a <select>: a sub-workflow may be
    // named by a placeholder ("{var.zielprozess}"), and it stays referencable
    // even if the file is added after this one was written.
    input = document.createElement("input");
    input.type = "text";
    input.setAttribute("list", "workflow-names");
    input.placeholder = "Name des Workflows";
    input.value = step.params[fieldDef.name] ?? "";
    input.addEventListener("focus", () => {
      if (!editing) {
        pushUndo();
        editing = true;
      }
    });
    input.addEventListener("blur", () => {
      editing = false;
    });
  } else if (fieldDef.type === "variable") {
    // Free text with suggestions from the workflow's declared variables (see
    // the "Variablen" button) - a field that *writes* to a name, so it isn't
    // restricted to only declared ones (assign still creates new names too).
    input = document.createElement("input");
    input.type = "text";
    input.setAttribute("list", "variable-names");
    input.placeholder = "Variablenname";
    input.value = step.params[fieldDef.name] ?? "";
    input.addEventListener("focus", () => {
      if (!editing) {
        pushUndo();
        editing = true;
      }
    });
    input.addEventListener("blur", () => {
      editing = false;
    });
  } else {
    input = document.createElement("input");
    input.type = fieldDef.type === "number" ? "number" : "text";
    input.value = step.params[fieldDef.name] ?? "";
    // Snapshot once per edit session (on focus), not once per keystroke.
    input.addEventListener("focus", () => {
      if (!editing) {
        pushUndo();
        editing = true;
      }
    });
    input.addEventListener("blur", () => {
      editing = false;
    });
  }

  input.addEventListener("input", () => {
    const raw = fieldDef.type === "checkbox" ? input.checked : input.value;
    const value = fieldValue(fieldDef, raw);
    if (fieldDef.type === "json" && value === undefined) {
      return; // invalid JSON mid-typing - keep the last valid committed value untouched
    }
    if (value === undefined || value === false) {
      delete step.params[fieldDef.name];
    } else {
      step.params[fieldDef.name] = value;
    }
    // Only the card's summary line depends on a parameter, so patch that in
    // place - a full re-render here would tear the focused input out mid-edit.
    refreshSelectedSummary();
  });

  if (fieldDef.type === "checkbox") {
    wrap.append(input, label);
  } else if (fieldDef.type === "hotkey") {
    const recordBtn = document.createElement("button");
    recordBtn.type = "button";
    recordBtn.className = "btn-icon hotkey-record";
    recordBtn.innerHTML = ICONS.keyboard;
    recordBtn.title = "Tastenkombination aufnehmen";
    recordBtn.setAttribute("aria-label", "Tastenkombination aufnehmen");
    recordBtn.addEventListener("click", () => recordHotkey(input, step, fieldDef.name));
    const inputRow = document.createElement("div");
    inputRow.className = "hotkey-row";
    inputRow.append(input, recordBtn);
    wrap.append(label, inputRow);
  } else {
    wrap.append(label, input);
  }
  return wrap;
}

// Independent of an enclosing `try`/`catch`: lets any single step retry
// itself a few times or simply be skipped over on failure (see engine.py's
// _run_step_with_policy), without wrapping it in a try block.
function renderErrorHandling(step) {
  const wrap = document.createElement("div");
  wrap.className = "field error-handling";

  const label = document.createElement("label");
  label.textContent = "Bei Fehler (unabhängig von Versuchen/Bei Fehler)";
  const select = document.createElement("select");
  const options = [
    { value: "", text: "Abbrechen (Standard)" },
    { value: "continue", text: "Fortsetzen" },
    { value: "retry", text: "Wiederholen" },
  ];
  for (const opt of options) {
    const el2 = document.createElement("option");
    el2.value = opt.value;
    el2.textContent = opt.text;
    if ((step.on_error || "") === opt.value) el2.selected = true;
    select.appendChild(el2);
  }
  select.addEventListener("change", () => {
    pushUndo();
    step.on_error = select.value;
    renderProperties(); // retry fields only make sense once "retry" is picked
  });
  wrap.append(label, select);

  if (step.on_error === "retry") {
    const row = document.createElement("div");
    row.className = "field-row";

    const countWrap = document.createElement("div");
    countWrap.className = "field";
    const countLabel = document.createElement("label");
    countLabel.textContent = "Anzahl Versuche";
    const countInput = document.createElement("input");
    countInput.type = "number";
    countInput.min = "1";
    countInput.value = step.retry_count ?? 3;
    countInput.addEventListener("focus", () => pushUndo());
    countInput.addEventListener("input", () => {
      step.retry_count = parseInt(countInput.value, 10) || 1;
    });
    countWrap.append(countLabel, countInput);

    const delayWrap = document.createElement("div");
    delayWrap.className = "field";
    const delayLabel = document.createElement("label");
    delayLabel.textContent = "Wartezeit zwischen Versuchen (s)";
    const delayInput = document.createElement("input");
    delayInput.type = "number";
    delayInput.min = "0";
    delayInput.step = "0.5";
    delayInput.value = step.retry_delay ?? 2;
    delayInput.addEventListener("focus", () => pushUndo());
    delayInput.addEventListener("input", () => {
      step.retry_delay = parseFloat(delayInput.value) || 0;
    });
    delayWrap.append(delayLabel, delayInput);

    row.append(countWrap, delayWrap);
    wrap.appendChild(row);
  }

  return wrap;
}

function renderProperties() {
  const body = el("properties-body");
  const title = el("properties-title");
  body.innerHTML = "";

  const step = state.selected;
  if (!step) {
    title.textContent = "Eigenschaften";
    const hint = document.createElement("p");
    hint.className = "properties-empty";
    hint.textContent = "Wähle eine Aktivität im Ablauf aus, um ihre Parameter zu bearbeiten.";
    body.appendChild(hint);
    return;
  }

  const actions = schema[state.backend] || {};
  const meta = activityMeta(step.action);
  title.textContent = meta.label;

  if (meta.description) {
    const desc = document.createElement("p");
    desc.className = "properties-desc";
    desc.textContent = meta.description;
    body.appendChild(desc);
  }

  const actionWrap = document.createElement("div");
  actionWrap.className = "field";
  const actionLabel = document.createElement("label");
  actionLabel.textContent = "Aktivität";
  const actionSelect = document.createElement("select");
  for (const actionName of Object.keys(actions)) {
    const opt = document.createElement("option");
    opt.value = actionName;
    opt.textContent = activityMeta(actionName).label;
    if (actionName === step.action) opt.selected = true;
    actionSelect.appendChild(opt);
  }
  actionSelect.addEventListener("change", () => {
    pushUndo();
    step.action = actionSelect.value;
    step.params = {};
    step.slots = emptySlotsFor(step.action, actions);
    renderSteps();
  });
  actionWrap.append(actionLabel, actionSelect);
  body.appendChild(actionWrap);

  // Branch fields are structural: they are edited on the canvas as drop
  // targets, not as form inputs, so the panel skips them.
  for (const fieldDef of actions[step.action] || []) {
    if (fieldDef.type === "steps" || fieldDef.type === "cases") continue;
    body.appendChild(renderField(step, fieldDef));
  }

  const saveAsWrap = document.createElement("div");
  saveAsWrap.className = "field";
  const saveAsLabel = document.createElement("label");
  saveAsLabel.textContent = "Ergebnis speichern als (optional)";
  const saveAsInput = document.createElement("input");
  saveAsInput.type = "text";
  saveAsInput.placeholder = "Variablenname";
  saveAsInput.value = step.save_as || "";
  saveAsInput.addEventListener("focus", () => pushUndo());
  saveAsInput.addEventListener("input", () => {
    step.save_as = saveAsInput.value || "";
  });
  saveAsWrap.append(saveAsLabel, saveAsInput);
  body.appendChild(saveAsWrap);

  body.appendChild(renderErrorHandling(step));

  const fieldDefs = actions[step.action] || [];
  const hasSelectorField = fieldDefs.some((f) => f.name === "selector");
  const hasDesktopTargetFields = fieldDefs.some((f) => ["control_type", "title", "auto_id"].includes(f.name));
  if (hasSelectorField || hasDesktopTargetFields) {
    const pickBtn = document.createElement("button");
    pickBtn.type = "button";
    pickBtn.className = "btn btn-pick";
    pickBtn.innerHTML = ICONS.target + "<span>Element auf dem Bildschirm wählen</span>";
    pickBtn.addEventListener("click", () => {
      if (hasSelectorField) pickWebSelector(step);
      else pickDesktopSelector(step);
    });
    body.appendChild(pickBtn);
  }
}

function renderRecordingControls() {
  const wrap = document.createElement("div");
  wrap.className = "record-controls";
  const btn = document.createElement("button");
  btn.className = "btn" + (currentRecordId ? " btn-recording" : "");
  btn.innerHTML = currentRecordId
    ? ICONS.stop + "<span>Aufnahme stoppen</span>"
    : ICONS.dot + "<span>Aufnahme starten</span>";
  btn.addEventListener("click", () => (currentRecordId ? stopRecording() : startRecording()));
  wrap.appendChild(btn);
  if (currentRecordId) {
    const hint = document.createElement("span");
    hint.className = "record-hint";
    hint.textContent = "Klicks/Eingaben in der Zielanwendung werden live als Schritte übernommen...";
    wrap.appendChild(hint);
  }
  return wrap;
}

function findNavigateUrl() {
  const navStep = state.steps.find((s) => s.action === "navigate" && s.params.url);
  return navStep ? navStep.params.url : "";
}

function showPickStatus(text) {
  const banner = document.createElement("div");
  banner.className = "pick-banner";
  banner.textContent = text;
  document.body.appendChild(banner);
  return banner;
}

function hidePickStatus(banner) {
  banner.remove();
}

// Captures the next key combo pressed anywhere in the page (like an OS
// "record a shortcut" field) and writes it as a "ctrl+shift+s"-style string -
// translated to each backend's own hotkey syntax at run time (see
// backends/desktop.py's/web.py's _translate_hotkey).
function recordHotkey(input, step, fieldName) {
  const previous = input.value;
  input.value = "Taste(n) drücken...";
  const banner = showPickStatus("Bitte jetzt die gewünschte Tastenkombination drücken (Esc zum Abbrechen)...");

  const cleanup = () => {
    window.removeEventListener("keydown", handler, true);
    hidePickStatus(banner);
  };

  const handler = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") {
      input.value = previous;
      cleanup();
      return;
    }
    if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) {
      return; // wait for a real key while modifiers are held
    }
    const parts = [];
    if (e.ctrlKey) parts.push("ctrl");
    if (e.altKey) parts.push("alt");
    if (e.shiftKey) parts.push("shift");
    if (e.metaKey) parts.push("win");
    parts.push(e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase());
    const combo = parts.join("+");
    pushUndo();
    input.value = combo;
    step.params[fieldName] = combo;
    cleanup();
  };

  window.addEventListener("keydown", handler, true);
}

async function pickWebSelector(step) {
  let url = findNavigateUrl();
  if (!url) {
    url = window.prompt("URL der Seite, auf der ausgewählt werden soll:", "https://");
    if (!url) return;
  }
  const banner = showPickStatus("Browser öffnet sich – bitte im Fenster auf das gewünschte Element klicken...");
  try {
    const res = await fetch("/api/pick/web", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (data.ok) {
      pushUndo();
      step.params.selector = data.selector;
      renderSteps();
    } else {
      toast("Auswahl fehlgeschlagen: " + data.error, "error");
    }
  } catch (err) {
    toast("Auswahl fehlgeschlagen: " + err, "error");
  } finally {
    hidePickStatus(banner);
  }
}

function findDesktopScope() {
  const launchStep = state.steps.find((s) => s.action === "launch" && s.params.path);
  if (launchStep) return { focus_path: launchStep.params.path };
  const connectStep = state.steps.find((s) => s.action === "connect" && s.params.title);
  if (connectStep) return { focus_title: connectStep.params.title };
  return null;
}

async function pickDesktopSelector(step) {
  const scope = findDesktopScope();
  const raw = window.prompt(
    scope
      ? "Timeout (Sekunden) vor der Aufnahme — die Zielanwendung wird automatisch in den Vordergrund geholt:"
      : "Kein Scope (launch/connect-Schritt) im Workflow gefunden. Timeout (Sekunden), um manuell zur Zielanwendung zu wechseln:",
    scope ? "0" : "3"
  );
  if (raw === null) return;
  const delay = Math.max(0, Number(raw) || 0);

  const banner = showPickStatus(
    delay > 0
      ? `Wechsle jetzt zur Zielanwendung – Aufnahme startet in ${delay}s...`
      : "Bitte jetzt auf das gewünschte Element im Zielfenster klicken..."
  );
  try {
    const res = await fetch("/api/pick/desktop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delay, ...(scope || {}) }),
    });
    const data = await res.json();
    if (data.ok) {
      pushUndo();
      if (data.control_type) step.params.control_type = data.control_type;
      if (data.auto_id) step.params.auto_id = data.auto_id;
      if (data.title) step.params.title = data.title;
      renderSteps();
    } else {
      toast("Auswahl fehlgeschlagen: " + data.error, "error");
    }
  } catch (err) {
    toast("Auswahl fehlgeschlagen: " + err, "error");
  } finally {
    hidePickStatus(banner);
  }
}

async function startRecording() {
  const scope = findDesktopScope();
  if (!scope) {
    toast("Aufnahme benötigt einen Scope: der erste Schritt muss 'launch' oder 'connect' sein.", "error");
    return;
  }
  const res = await fetch("/api/record/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scope),
  });
  const data = await res.json();
  if (!data.ok) {
    toast("Aufnahme konnte nicht gestartet werden: " + (data.error || res.status), "error");
    return;
  }
  currentRecordId = data.record_id;
  renderSteps();

  const source = new EventSource(`/api/record/${currentRecordId}/stream`);
  recordingSource = source;
  source.addEventListener("step", (event) => {
    const s = JSON.parse(event.data);
    pushUndo();
    state.steps.push(rawStepToModel({ action: s.action, ...(s.params || {}) }, schema[state.backend] || {}));
    renderSteps();
  });
  source.addEventListener("stopped", () => {
    source.close();
    recordingSource = null;
    currentRecordId = null;
    renderSteps();
  });
  source.onerror = () => {
    source.close();
    recordingSource = null;
    currentRecordId = null;
    renderSteps();
  };
}

async function stopRecording() {
  if (!currentRecordId) return;
  await fetch(`/api/record/${currentRecordId}/stop`, { method: "POST" });
}

function currentWorkflowPayload() {
  const payload = {
    name: el("wf-name").value || "workflow",
    backend: state.backend,
    browser_channel: state.backend === "web" ? el("wf-browser-channel").value || undefined : undefined,
    steps: modelStepsToRaw(state.steps),
  };
  if (Object.keys(state.variables).length) payload.variables = state.variables;
  return payload;
}

function saveWorkflowAs(name, payload, { overwrite }) {
  return fetch(`/api/workflows/${encodeURIComponent(name)}?overwrite=${overwrite}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function saveWorkflow() {
  const payload = currentWorkflowPayload();
  // Saving back over the workflow that's open is the normal case and stays
  // silent; saving under a name that belongs to a *different* workflow (the
  // name field was edited) would otherwise destroy it without a word.
  const isSaveOverOpenWorkflow = payload.name === el("wf-load").value;
  let res = await saveWorkflowAs(payload.name, payload, { overwrite: isSaveOverOpenWorkflow });
  if (res.status === 409) {
    const confirmed = await confirmDialog(
      `Es gibt bereits einen Workflow "${payload.name}". Soll er überschrieben werden?`,
      "Überschreiben"
    );
    if (!confirmed) return;
    res = await saveWorkflowAs(payload.name, payload, { overwrite: true });
  }
  if (res.ok) {
    await loadWorkflowList();
    el("wf-load").value = payload.name;
    toast("Workflow gespeichert.", "success");
  } else {
    const err = await res.json();
    toast("Speichern fehlgeschlagen: " + (err.error || res.status), "error");
  }
}

function appendLog(line) {
  const out = el("log-output");
  out.textContent += line + "\n";
  out.scrollTop = out.scrollHeight;
}

function clearPausedHighlight() {
  document.querySelectorAll(".step-card.paused-at").forEach((c) => c.classList.remove("paused-at"));
}

// Matched in JS rather than via an attribute selector because a path segment
// can be a user-typed switch case value ("DE", but also `"` or `\`), which
// would need escaping to be safe to interpolate into a selector string.
function highlightPausedStep(path) {
  if (!path) return; // job paused by an older build that didn't report a path
  for (const card of document.querySelectorAll(".step-card")) {
    if (card.dataset.stepPath === path) {
      card.classList.add("paused-at");
      return;
    }
  }
}

function renderVariablesWatch(variables) {
  const box = el("variables-watch");
  const names = Object.keys(variables || {});
  if (!names.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML =
    "<div class='variables-watch-title'>Variablen</div>" +
    names
      .map((name) => {
        const value = variables[name];
        const rendered = typeof value === "string" ? value : JSON.stringify(value);
        return `<div class="variables-watch-row"><span class="variables-watch-name">${escapeHtml(name)}</span><span class="variables-watch-value">${escapeHtml(rendered)}</span></div>`;
      })
      .join("");
}

function hideVariablesWatch() {
  const box = el("variables-watch");
  box.classList.add("hidden");
  box.innerHTML = "";
}

async function runWorkflow() {
  const payload = currentWorkflowPayload();
  const queueName = el("wf-queue").value.trim();
  if (queueName) payload.queue_name = queueName;

  el("log-output").textContent = "";
  el("log-screenshot").classList.add("hidden");
  el("log-status").textContent = queueName ? `Läuft (Queue "${queueName}")...` : "Läuft...";
  el("log-status").className = "";
  el("btn-continue").classList.add("hidden");
  el("btn-stop").classList.remove("hidden");
  el("btn-stop").disabled = false;
  el("log-panel").classList.remove("hidden");
  clearPausedHighlight();
  hideVariablesWatch();

  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json();
    appendLog("Fehler: " + (err.error || res.status));
    el("log-status").textContent = "Fehler";
    el("log-status").className = "status-error";
    return;
  }
  const { job_id } = await res.json();
  currentJobId = job_id;
  const source = new EventSource(`/api/run/${job_id}/stream`);

  source.onmessage = (event) => {
    appendLog(JSON.parse(event.data));
  };

  source.addEventListener("paused", (event) => {
    const { index: stepIndex, action, path, variables } = JSON.parse(event.data);
    appendLog(`>> Haltepunkt bei Schritt ${stepIndex} (${action})`);
    el("log-status").textContent = `Angehalten bei Schritt ${stepIndex}`;
    el("log-status").className = "status-paused";
    el("btn-continue").classList.remove("hidden");
    clearPausedHighlight();
    highlightPausedStep(path);
    renderVariablesWatch(variables);
  });

  source.addEventListener("done", (event) => {
    const status = JSON.parse(event.data);
    if (status.startsWith("success")) {
      el("log-status").textContent = "Erfolgreich";
      el("log-status").className = "status-success";
    } else if (status.startsWith("cancelled")) {
      el("log-status").textContent = "Abgebrochen";
      el("log-status").className = "status-cancelled";
      appendLog(">> Workflow abgebrochen");
    } else {
      el("log-status").textContent = "Fehlgeschlagen";
      el("log-status").className = "status-error";
      appendLog(status);
    }
    el("btn-continue").classList.add("hidden");
    el("btn-stop").classList.add("hidden");
    clearPausedHighlight();
    hideVariablesWatch();
    currentJobId = null;
    showScreenshotIfAny(payload);
    source.close();
  });

  source.onerror = () => {
    source.close();
  };
}

async function continueRun() {
  if (!currentJobId) return;
  el("btn-continue").classList.add("hidden");
  el("log-status").textContent = "Läuft...";
  el("log-status").className = "";
  clearPausedHighlight();
  await fetch(`/api/run/${currentJobId}/continue`, { method: "POST" });
}

async function stopRun() {
  if (!currentJobId) return;
  el("btn-stop").disabled = true;
  el("log-status").textContent = "Wird gestoppt...";
  await fetch(`/api/run/${currentJobId}/stop`, { method: "POST" });
}

function showScreenshotIfAny(payload) {
  const shot = [...payload.steps].reverse().find((s) => s.action === "screenshot");
  if (!shot || !shot.path) return;
  const box = el("log-screenshot");
  box.innerHTML = "";
  const img = document.createElement("img");
  img.src = `/api/screenshot?path=${encodeURIComponent(shot.path)}&t=${Date.now()}`;
  box.appendChild(img);
  box.classList.remove("hidden");
}

async function loadQueueNames() {
  const res = await fetch("/api/queues");
  const queues = await res.json();
  const datalist = el("queue-names");
  datalist.innerHTML = "";
  for (const q of queues) {
    const opt = document.createElement("option");
    opt.value = q.name;
    datalist.appendChild(opt);
  }
  return queues;
}

function statusBadges(counts) {
  const parts = [];
  for (const key of ["new", "in_progress", "success", "failed"]) {
    const value = counts[`${key}_count`] || 0;
    if (value === 0) continue;
    parts.push(`<span class="queue-count-badge ${key}">${key}: ${value}</span>`);
  }
  return parts.join("") || '<span class="queue-count-badge">leer</span>';
}

async function renderQueuesPanel() {
  const container = el("queues-list");
  container.innerHTML = "Lädt...";
  const queues = await loadQueueNames();
  if (queues.length === 0) {
    container.innerHTML = '<p style="color:var(--muted)">Noch keine Queues. Über die API anlegen: '
      + '<code>POST /api/queues/&lt;name&gt;/items</code></p>';
    return;
  }
  container.innerHTML = "";
  for (const q of queues) {
    const card = document.createElement("div");
    card.className = "queue-card";

    const head = document.createElement("div");
    head.className = "queue-card-head";
    const headIcon = document.createElement("span");
    headIcon.innerHTML = ICONS.clipboard;
    const headName = document.createElement("span");
    headName.style.flex = "1";
    headName.textContent = q.name;
    const delQueueBtn = document.createElement("button");
    delQueueBtn.className = "btn-icon danger";
    delQueueBtn.innerHTML = ICONS.trash;
    delQueueBtn.title = "Queue löschen";
    delQueueBtn.setAttribute("aria-label", `Queue "${q.name}" löschen`);
    delQueueBtn.addEventListener("click", async () => {
      if (!(await confirmDialog(`Queue "${q.name}" inklusive aller Items wirklich löschen?`))) return;
      await fetch(`/api/queues/${encodeURIComponent(q.name)}`, { method: "DELETE" });
      await renderQueuesPanel();
      await loadQueueNames();
      toast(`Queue "${q.name}" gelöscht.`, "success");
    });
    head.append(headIcon, headName, delQueueBtn);
    card.appendChild(head);

    const counts = document.createElement("div");
    counts.className = "queue-counts";
    counts.innerHTML = statusBadges(q);
    card.appendChild(counts);

    const items = await (await fetch(`/api/queues/${encodeURIComponent(q.name)}/items`)).json();
    if (items.length > 0) {
      const table = document.createElement("table");
      table.className = "queue-items-table";
      table.innerHTML =
        "<tr><th>#</th><th>Status</th><th>Payload</th></tr>" +
        items
          .slice(0, 20)
          .map(
            (item) =>
              `<tr><td>${item.id}</td><td class="queue-item-status ${item.status}">${item.status}</td>` +
              `<td>${escapeHtml(item.payload)}</td></tr>`
          )
          .join("");
      card.appendChild(table);
    }
    container.appendChild(card);
  }
}

async function renderCredentialsPanel() {
  const container = el("credentials-list");
  container.innerHTML = "Lädt...";
  const names = await (await fetch("/api/credentials")).json();
  if (names.length === 0) {
    container.innerHTML = '<p style="color:var(--muted)">Noch keine Anmeldedaten gespeichert.</p>';
    return;
  }
  container.innerHTML = "";
  for (const name of names) {
    const row = document.createElement("div");
    row.className = "list-row";
    const label = document.createElement("span");
    label.className = "list-row-name";
    label.textContent = name;
    const delBtn = document.createElement("button");
    delBtn.className = "btn-icon danger";
    delBtn.textContent = "✕";
    delBtn.title = "Löschen";
    delBtn.setAttribute("aria-label", `Anmeldedaten "${name}" löschen`);
    delBtn.addEventListener("click", async () => {
      if (!(await confirmDialog(`Anmeldedaten "${name}" wirklich löschen?`))) return;
      await fetch(`/api/credentials/${encodeURIComponent(name)}`, { method: "DELETE" });
      renderCredentialsPanel();
      toast(`Anmeldedaten "${name}" gelöscht.`, "success");
    });
    row.append(label, delBtn);
    container.appendChild(row);
  }
}

async function renderGlobalsPanel() {
  const container = el("globals-list");
  container.innerHTML = "Lädt...";
  const entries = await (await fetch("/api/globals")).json();
  if (entries.length === 0) {
    container.innerHTML = '<p style="color:var(--muted)">Noch keine globalen Variablen gespeichert.</p>';
    return;
  }
  container.innerHTML = "";
  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = "list-row";

    const info = document.createElement("div");
    info.style.flex = "1";
    info.style.minWidth = "0";
    const label = document.createElement("div");
    label.className = "list-row-name";
    label.textContent = entry.name;
    const value = document.createElement("div");
    value.className = "list-row-meta global-value";
    // Objects and lists are shown as JSON - the same form the input accepts back
    value.textContent = typeof entry.value === "string" ? entry.value : JSON.stringify(entry.value);
    info.append(label, value);

    const editBtn = document.createElement("button");
    editBtn.className = "btn-icon";
    editBtn.innerHTML = ICONS.pencil;
    editBtn.title = "Bearbeiten";
    editBtn.setAttribute("aria-label", `Globale Variable "${entry.name}" bearbeiten`);
    editBtn.addEventListener("click", () => {
      el("global-name").value = entry.name;
      el("global-value").value =
        typeof entry.value === "string" ? entry.value : JSON.stringify(entry.value);
      el("global-value").focus();
    });

    const delBtn = document.createElement("button");
    delBtn.className = "btn-icon danger";
    delBtn.textContent = "✕";
    delBtn.title = "Löschen";
    delBtn.setAttribute("aria-label", `Globale Variable "${entry.name}" löschen`);
    delBtn.addEventListener("click", async () => {
      if (!(await confirmDialog(`Globale Variable "${entry.name}" wirklich löschen?`))) return;
      await fetch(`/api/globals/${encodeURIComponent(entry.name)}`, { method: "DELETE" });
      renderGlobalsPanel();
      toast(`Globale Variable "${entry.name}" gelöscht.`, "success");
    });

    row.append(info, editBtn, delBtn);
    container.appendChild(row);
  }
}

async function addGlobal() {
  const name = el("global-name").value.trim();
  const value = el("global-value").value;
  if (!name) {
    toast("Bitte einen Namen angeben.", "error");
    return;
  }
  const res = await fetch("/api/globals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, value }),
  });
  const data = await res.json();
  if (!res.ok) {
    toast("Speichern fehlgeschlagen: " + (data.error || res.status), "error");
    return;
  }
  el("global-name").value = "";
  el("global-value").value = "";
  renderGlobalsPanel();
  toast(`Globale Variable "${name}" gespeichert.`, "success");
}

// --- declared workflow variables (per-workflow, saved as part of its own
// YAML - see models.Workflow.variables - not the installation-wide globals
// above, which live in orchestrator.db instead) ------------------------------

// Same "try JSON, fall back to plain string" rule as the server applies to a
// global variable's value (studio/app.py's set_global_route) - kept in sync
// here since declared-variable defaults never leave the browser to be parsed
// server-side; a number stays a number, a list stays a list.
function parseLooseValue(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function refreshVariableNamesDatalist() {
  const datalist = el("variable-names");
  datalist.innerHTML = "";
  for (const name of Object.keys(state.variables)) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    datalist.appendChild(opt);
  }
}

function renderVariableDeclarations() {
  const container = el("variables-decl-list");
  const names = Object.keys(state.variables);
  if (names.length === 0) {
    container.innerHTML = '<p style="color:var(--muted)">Noch keine Variablen deklariert.</p>';
    return;
  }
  container.innerHTML = "";
  for (const name of names) {
    const value = state.variables[name];
    const row = document.createElement("div");
    row.className = "list-row";

    const info = document.createElement("div");
    info.style.flex = "1";
    info.style.minWidth = "0";
    const label = document.createElement("div");
    label.className = "list-row-name";
    label.textContent = name;
    const valueEl = document.createElement("div");
    valueEl.className = "list-row-meta";
    valueEl.textContent = value === null ? "(kein Startwert)" : typeof value === "string" ? value : JSON.stringify(value);
    info.append(label, valueEl);

    const editBtn = document.createElement("button");
    editBtn.className = "btn-icon";
    editBtn.innerHTML = ICONS.pencil;
    editBtn.title = "Bearbeiten";
    editBtn.setAttribute("aria-label", `Variable "${name}" bearbeiten`);
    editBtn.addEventListener("click", () => {
      el("var-decl-name").value = name;
      el("var-decl-default").value = value === null ? "" : typeof value === "string" ? value : JSON.stringify(value);
      el("var-decl-default").focus();
    });

    const delBtn = document.createElement("button");
    delBtn.className = "btn-icon danger";
    delBtn.textContent = "✕";
    delBtn.title = "Löschen";
    delBtn.setAttribute("aria-label", `Variable "${name}" löschen`);
    delBtn.addEventListener("click", async () => {
      if (!(await confirmDialog(`Variable "${name}" wirklich löschen?`))) return;
      pushUndo();
      delete state.variables[name];
      refreshVariableNamesDatalist();
      renderVariableDeclarations();
      toast(`Variable "${name}" gelöscht.`, "success");
    });

    row.append(info, editBtn, delBtn);
    container.appendChild(row);
  }
}

function addVariableDeclaration() {
  const name = el("var-decl-name").value.trim();
  const rawValue = el("var-decl-default").value;
  if (!name) {
    toast("Bitte einen Namen angeben.", "error");
    return;
  }
  if (["global", "item", "var"].includes(name)) {
    toast(`"${name}" ist ein reservierter Name.`, "error");
    return;
  }
  pushUndo();
  state.variables[name] = rawValue === "" ? null : parseLooseValue(rawValue);
  el("var-decl-name").value = "";
  el("var-decl-default").value = "";
  refreshVariableNamesDatalist();
  renderVariableDeclarations();
  toast(`Variable "${name}" gespeichert.`, "success");
}

function openVariablesOverlay() {
  renderVariableDeclarations();
  el("variables-overlay").classList.remove("hidden");
}

function closeVariablesOverlay() {
  el("variables-overlay").classList.add("hidden");
}

async function addCredential() {
  const name = el("credential-name").value.trim();
  const value = el("credential-value").value;
  if (!name || !value) {
    toast("Bitte Name und Wert angeben.", "error");
    return;
  }
  const res = await fetch("/api/credentials", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, value }),
  });
  const data = await res.json();
  if (!res.ok) {
    toast("Speichern fehlgeschlagen: " + (data.error || res.status), "error");
    return;
  }
  toast(`Anmeldedaten "${name}" gespeichert.`, "success");
  el("credential-name").value = "";
  el("credential-value").value = "";
  await renderCredentialsPanel();
}

function cronDescription(cron) {
  const presets = {
    "* * * * *": "jede Minute",
    "0 * * * *": "stündlich",
    "0 0 * * *": "täglich um Mitternacht",
    "0 2 * * *": "täglich um 2 Uhr",
    "0 0 * * 1": "wöchentlich (Montag)",
  };
  return presets[cron] || cron;
}

async function renderSchedulesPanel() {
  const container = el("schedules-list");
  container.innerHTML = "Lädt...";
  const schedules = await (await fetch("/api/schedules")).json();
  if (schedules.length === 0) {
    container.innerHTML = '<p style="color:var(--muted)">Noch keine Zeitpläne.</p>';
    return;
  }
  container.innerHTML = "";
  for (const s of schedules) {
    const row = document.createElement("div");
    row.className = "list-row";

    const info = document.createElement("div");
    info.style.flex = "1";
    const name = document.createElement("div");
    name.className = "list-row-name";
    name.textContent = s.name;
    const meta = document.createElement("div");
    meta.className = "list-row-meta";
    meta.textContent =
      cronDescription(s.cron_expr) +
      (s.queue_name ? ` · Queue: ${s.queue_name}` : "") +
      (s.last_run_at ? ` · zuletzt: ${new Date(s.last_run_at).toLocaleString()}` : " · noch nie gelaufen");
    info.append(name, meta);

    const toggleBtn = document.createElement("button");
    toggleBtn.className = "btn-icon";
    toggleBtn.innerHTML = s.enabled ? ICONS.pause : ICONS.play;
    toggleBtn.title = s.enabled ? "Deaktivieren" : "Aktivieren";
    toggleBtn.setAttribute("aria-label", s.enabled ? "Zeitplan deaktivieren" : "Zeitplan aktivieren");
    toggleBtn.addEventListener("click", async () => {
      await fetch(`/api/schedules/${s.id}/toggle`, { method: "POST" });
      renderSchedulesPanel();
    });

    const delBtn = document.createElement("button");
    delBtn.className = "btn-icon danger";
    delBtn.textContent = "✕";
    delBtn.title = "Löschen";
    delBtn.setAttribute("aria-label", `Zeitplan "${s.name}" löschen`);
    delBtn.addEventListener("click", async () => {
      if (!(await confirmDialog(`Zeitplan "${s.name}" wirklich löschen?`))) return;
      await fetch(`/api/schedules/${s.id}`, { method: "DELETE" });
      renderSchedulesPanel();
      toast(`Zeitplan "${s.name}" gelöscht.`, "success");
    });

    row.append(info, toggleBtn, delBtn);
    container.appendChild(row);
  }
}

async function addSchedule() {
  const name = el("schedule-name").value.trim();
  const cronExpr = el("schedule-cron").value.trim();
  if (!name || !cronExpr) {
    toast("Bitte Name und Cron-Ausdruck angeben.", "error");
    return;
  }
  const payload = currentWorkflowPayload();
  const queueName = el("wf-queue").value.trim();
  const res = await fetch("/api/schedules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, cron_expr: cronExpr, workflow: payload, queue_name: queueName || undefined }),
  });
  const data = await res.json();
  if (!res.ok) {
    toast("Planen fehlgeschlagen: " + (data.error || res.status), "error");
    return;
  }
  el("schedule-name").value = "";
  el("schedule-cron").value = "";
  await renderSchedulesPanel();
  toast(`Zeitplan "${name}" angelegt.`, "success");
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

async function importExcelToQueue() {
  const name = el("excel-queue-name").value.trim();
  const fileInput = el("excel-file");
  const file = fileInput.files[0];
  if (!name || !file) {
    toast("Bitte Queue-Name und Excel-Datei angeben.", "error");
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`/api/queues/${encodeURIComponent(name)}/import-excel`, {
    method: "POST",
    body: formData,
  });
  const data = await res.json();
  if (!res.ok) {
    toast("Import fehlgeschlagen: " + (data.error || res.status), "error");
    return;
  }
  fileInput.value = "";
  el("excel-queue-name").value = "";
  await renderQueuesPanel();
  await loadQueueNames();
  toast(`${data.added} Zeile(n) importiert.`, "success");
}

// --- Orchestrator > Workflows: list, open, rename, duplicate, delete -------

function startInlineRename(row, oldName, mode) {
  const original = Array.from(row.children);
  original.forEach((c) => (c.style.display = "none"));

  const editRow = document.createElement("div");
  editRow.className = "inline-edit-row";
  const input = document.createElement("input");
  input.type = "text";
  input.value = mode === "duplicate" ? `${oldName} (Kopie)` : oldName;

  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "btn-icon";
  confirmBtn.innerHTML = ICONS.check;
  confirmBtn.title = mode === "duplicate" ? "Duplizieren" : "Umbenennen";

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "btn-icon";
  cancelBtn.textContent = "✕";
  cancelBtn.title = "Abbrechen";

  const cleanup = () => {
    editRow.remove();
    original.forEach((c) => (c.style.display = ""));
  };
  cancelBtn.addEventListener("click", cleanup);

  const commit = async () => {
    const newName = input.value.trim();
    if (!newName || newName === oldName) {
      cleanup();
      return;
    }
    const data = await (await fetch(`/api/workflows/${encodeURIComponent(oldName)}`)).json();
    data.name = newName;
    // Never clobber a different workflow that happens to carry the target name:
    // ask the server to refuse first, then let the user decide (the edit stays
    // open on refusal, so they can just pick another name).
    let res = await saveWorkflowAs(newName, data, { overwrite: false });
    if (res.status === 409) {
      const confirmed = await confirmDialog(
        `Es gibt bereits einen Workflow "${newName}". Soll er überschrieben werden?`,
        "Überschreiben"
      );
      if (!confirmed) return;
      res = await saveWorkflowAs(newName, data, { overwrite: true });
    }
    if (!res.ok) {
      const err = await res.json();
      toast("Fehlgeschlagen: " + (err.error || res.status), "error");
      return;
    }
    if (mode === "rename") {
      await fetch(`/api/workflows/${encodeURIComponent(oldName)}`, { method: "DELETE" });
    }
    await renderWorkflowsPanel();
    await loadWorkflowList();
    toast(
      mode === "rename" ? `Workflow umbenannt in "${newName}".` : `Workflow als "${newName}" dupliziert.`,
      "success"
    );
  };

  confirmBtn.addEventListener("click", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") cleanup();
  });

  editRow.append(input, confirmBtn, cancelBtn);
  row.appendChild(editRow);
  input.focus();
  input.select();
}

async function renderWorkflowsPanel() {
  const container = el("workflows-list");
  container.innerHTML = "Lädt...";
  const names = await (await fetch("/api/workflows")).json();
  if (names.length === 0) {
    container.innerHTML = '<p style="color:var(--muted)">Noch keine Workflows gespeichert.</p>';
    return;
  }
  container.innerHTML = "";
  for (const name of names) {
    const row = document.createElement("div");
    row.className = "list-row";

    const label = document.createElement("span");
    label.className = "list-row-name";
    label.textContent = name;

    const openBtn = document.createElement("button");
    openBtn.className = "btn-icon";
    openBtn.innerHTML = ICONS.document;
    openBtn.title = "Im Builder öffnen";
    openBtn.setAttribute("aria-label", `Workflow "${name}" im Builder öffnen`);
    openBtn.addEventListener("click", async () => {
      await loadWorkflow(name);
      switchView("builder");
    });

    const renameBtn = document.createElement("button");
    renameBtn.className = "btn-icon";
    renameBtn.innerHTML = ICONS.pencil;
    renameBtn.title = "Umbenennen";
    renameBtn.setAttribute("aria-label", `Workflow "${name}" umbenennen`);
    renameBtn.addEventListener("click", () => startInlineRename(row, name, "rename"));

    const duplicateBtn = document.createElement("button");
    duplicateBtn.className = "btn-icon";
    duplicateBtn.innerHTML = ICONS.copy;
    duplicateBtn.title = "Duplizieren";
    duplicateBtn.setAttribute("aria-label", `Workflow "${name}" duplizieren`);
    duplicateBtn.addEventListener("click", () => startInlineRename(row, name, "duplicate"));

    const delBtn = document.createElement("button");
    delBtn.className = "btn-icon danger";
    delBtn.innerHTML = ICONS.trash;
    delBtn.title = "Löschen";
    delBtn.setAttribute("aria-label", `Workflow "${name}" löschen`);
    delBtn.addEventListener("click", async () => {
      if (!(await confirmDialog(`Workflow "${name}" wirklich löschen?`))) return;
      await fetch(`/api/workflows/${encodeURIComponent(name)}`, { method: "DELETE" });
      await renderWorkflowsPanel();
      await loadWorkflowList();
      toast(`Workflow "${name}" gelöscht.`, "success");
    });

    row.append(label, openBtn, renameBtn, duplicateBtn, delBtn);
    container.appendChild(row);
  }
}

// --- Runs: history list + log detail drawer, backed by the already-existing
// /api/jobs endpoints (previously persisted but never surfaced in the UI) ---

function formatDuration(job) {
  if (!job.started_at) return "wartet...";
  const start = new Date(job.started_at);
  const end = job.finished_at ? new Date(job.finished_at) : new Date();
  const secs = Math.max(0, Math.round((end - start) / 1000));
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

function buildRunRow(job) {
  const row = document.createElement("div");
  row.className = "list-row run-row";

  const badge = document.createElement("span");
  badge.className = `run-status-badge ${job.status}`;
  badge.textContent = job.status;

  const info = document.createElement("div");
  info.style.flex = "1";
  const name = document.createElement("div");
  name.className = "list-row-name";
  name.textContent = job.name;
  const meta = document.createElement("div");
  meta.className = "list-row-meta";
  meta.textContent =
    (job.queue_name ? `Queue: ${job.queue_name} · ` : "") +
    (job.started_at ? new Date(job.started_at).toLocaleString() : "wartet noch") +
    ` · ${formatDuration(job)}`;
  info.append(name, meta);

  row.append(badge, info);
  row.addEventListener("click", () => openRunDetail(job));
  return row;
}

async function openRunDetail(job) {
  el("run-detail-title").textContent = `${job.name} · ${job.status}`;
  el("run-detail-logs").textContent = "Lädt...";
  el("run-detail-panel").classList.remove("hidden");
  const logs = await (await fetch(`/api/jobs/${job.id}/logs`)).json();
  el("run-detail-logs").textContent = logs.length ? logs.map((l) => l.message).join("\n") : "Keine Logs für diesen Lauf.";
}

async function renderRunsView() {
  const container = el("runs-list");
  container.innerHTML = "Lädt...";
  const jobs = await (await fetch("/api/jobs")).json();
  if (jobs.length === 0) {
    container.innerHTML = '<p style="color:var(--muted)">Noch keine Läufe.</p>';
    return;
  }
  container.innerHTML = "";
  for (const job of jobs) {
    container.appendChild(buildRunRow(job));
  }
}

// --- Dashboard: aggregate KPIs from the existing endpoints, no new backend --

async function renderDashboard() {
  const statsBox = el("dashboard-stats");
  statsBox.innerHTML = '<p style="color:var(--muted)">Lädt...</p>';
  const [workflowNames, jobs, queues, schedules] = await Promise.all([
    fetch("/api/workflows").then((r) => r.json()),
    fetch("/api/jobs").then((r) => r.json()),
    fetch("/api/queues").then((r) => r.json()),
    fetch("/api/schedules").then((r) => r.json()),
  ]);

  const successCount = jobs.filter((j) => j.status === "success").length;
  const finishedCount = jobs.filter((j) => ["success", "error", "cancelled"].includes(j.status)).length;
  const successRate = finishedCount ? Math.round((successCount / finishedCount) * 100) : null;
  const backlog = queues.reduce((sum, q) => sum + (q.new_count || 0) + (q.in_progress_count || 0), 0);
  const activeSchedules = schedules.filter((s) => s.enabled).length;

  const tiles = [
    { value: workflowNames.length, label: "Workflows" },
    { value: jobs.length, label: "Läufe (letzte 100)" },
    { value: successRate === null ? "–" : `${successRate}%`, label: "Erfolgsquote" },
    { value: backlog, label: "Offene Queue-Items" },
    { value: activeSchedules, label: "Aktive Zeitpläne" },
  ];
  statsBox.innerHTML = tiles
    .map(
      (t) =>
        `<div class="stat-tile"><div class="stat-tile-value">${t.value}</div><div class="stat-tile-label">${t.label}</div></div>`
    )
    .join("");

  const recentBox = el("dashboard-recent-runs");
  const recent = jobs.slice(0, 5);
  if (recent.length === 0) {
    recentBox.innerHTML = '<p style="color:var(--muted)">Noch keine Läufe.</p>';
    return;
  }
  recentBox.innerHTML = "";
  for (const job of recent) {
    recentBox.appendChild(buildRunRow(job));
  }
}

// --- view navigation (Übersicht / Builder / Orchestrator / Runs) -----------

const VIEW_LOADERS = {
  dashboard: renderDashboard,
  orchestrator: renderWorkflowsPanel, // "Workflows" is the default active tab
  runs: renderRunsView,
};

function switchView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === name));
  document
    .querySelectorAll(".nav-item[data-view]")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  const loader = VIEW_LOADERS[name];
  if (loader) loader();
}

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document
    .querySelectorAll(".tab-panel")
    .forEach((p) => p.classList.toggle("active", p.dataset.tabPanel === name));
  if (name === "workflows") renderWorkflowsPanel();
  if (name === "queues") renderQueuesPanel();
  if (name === "credentials") renderCredentialsPanel();
  if (name === "globals") renderGlobalsPanel();
  if (name === "schedules") renderSchedulesPanel();
}

function startNewWorkflow() {
  el("wf-name").value = "Neuer Workflow";
  el("wf-load").value = "";
  el("wf-queue").value = "";
  state.backend = "web";
  el("wf-backend").value = "web";
  el("wf-browser-channel").value = "";
  updateBrowserChannelVisibility();
  state.steps = [newStepFor(state.backend)];
  state.variables = {};
  refreshVariableNamesDatalist();
  state.selected = null;
  undoStack = [];
  updateUndoButton();
  renderCatalog();
  renderSteps();
}

function init() {
  el("btn-add-step").addEventListener("click", () => {
    pushUndo();
    const step = newStepFor(state.backend);
    state.steps.push(step);
    state.selected = step;
    renderSteps();
  });

  el("steps").addEventListener("click", () => {
    state.selected = null;
    renderSteps();
  });
  el("btn-save").addEventListener("click", saveWorkflow);
  el("btn-run").addEventListener("click", runWorkflow);
  el("btn-continue").addEventListener("click", continueRun);
  el("btn-stop").addEventListener("click", stopRun);
  el("btn-undo").addEventListener("click", undo);
  el("btn-variables").addEventListener("click", openVariablesOverlay);
  el("btn-close-variables").addEventListener("click", closeVariablesOverlay);
  el("btn-add-var-decl").addEventListener("click", addVariableDeclaration);
  el("variables-overlay").addEventListener("click", (e) => {
    if (e.target === el("variables-overlay")) closeVariablesOverlay();
  });
  el("btn-logout").addEventListener("click", async () => {
    await fetch("/logout", { method: "POST" });
    location.href = "/";
  });
  el("btn-close-log").addEventListener("click", () => el("log-panel").classList.add("hidden"));
  el("btn-close-run-detail").addEventListener("click", () => el("run-detail-panel").classList.add("hidden"));

  el("btn-refresh-queues").addEventListener("click", renderQueuesPanel);
  el("btn-import-excel").addEventListener("click", importExcelToQueue);
  el("btn-add-credential").addEventListener("click", addCredential);
  el("btn-add-global").addEventListener("click", addGlobal);
  el("btn-add-schedule").addEventListener("click", addSchedule);
  el("btn-refresh-runs").addEventListener("click", renderRunsView);
  el("btn-quick-new-workflow").addEventListener("click", () => {
    startNewWorkflow();
    switchView("builder");
  });

  document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });
  document.querySelectorAll("[data-goto]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.goto));
  });
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  document.addEventListener("keydown", (e) => {
    const isUndoShortcut = (e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z";
    if (isUndoShortcut && document.activeElement.tagName !== "INPUT") {
      e.preventDefault();
      undo();
    }
  });

  el("wf-backend").addEventListener("change", (e) => {
    pushUndo();
    state.backend = e.target.value;
    state.steps = [];
    state.selected = null;
    updateBrowserChannelVisibility();
    renderCatalog();
    renderSteps();
  });

  el("wf-load").addEventListener("change", (e) => {
    if (e.target.value) loadWorkflow(e.target.value);
  });

  el("catalog-search").addEventListener("input", renderCatalog);

  Promise.all([loadSchema(), loadCatalog()]).then(() => {
    renderCatalog();
    initCatalogSortable();
    state.steps = [newStepFor(state.backend)];
    renderSteps();
  });
  loadWorkflowList();
  loadQueueNames();
  updateUndoButton();
  updateBrowserChannelVisibility();
  switchView("dashboard");
}

init();
