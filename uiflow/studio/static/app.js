let schema = { web: {}, desktop: {} };

let state = {
  backend: "web",
  steps: [],
};

let currentJobId = null;
let currentRecordId = null;
let recordingSource = null;

const el = (id) => document.getElementById(id);

function newStepFor(backend) {
  const firstAction = Object.keys(schema[backend])[0];
  return { action: firstAction, params: {}, breakpoint: false, save_as: "" };
}

// --- undo ---

let undoStack = [];
const MAX_UNDO = 50;

function snapshotState() {
  return JSON.stringify({ name: el("wf-name").value, backend: state.backend, steps: state.steps });
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
  state.steps = snap.steps;
  renderSteps();
  updateUndoButton();
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
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
}

async function loadWorkflow(name) {
  const res = await fetch(`/api/workflows/${encodeURIComponent(name)}`);
  if (!res.ok) return;
  const data = await res.json();
  el("wf-name").value = data.name;
  el("wf-backend").value = data.backend;
  state.backend = data.backend;
  state.steps = data.steps.map((s) => {
    const { action, breakpoint, save_as, ...params } = s;
    return { action, params, breakpoint: !!breakpoint, save_as: save_as || "" };
  });
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

// step-list conversions, shared between the top-level workflow and nested
// control-flow branches (if/switch/for_each/try bodies): the wire format
// (workflows/*.yaml, engine.py's Step.from_dict) is a flat dict per step
// ({action, ...params, breakpoint?, save_as?}); the editor works with a
// {action, params, breakpoint, save_as} "model" shape instead.
function rawStepToModel(raw) {
  const { action, breakpoint, save_as, ...params } = raw;
  return { action, params, breakpoint: !!breakpoint, save_as: save_as || "" };
}
function modelStepToRaw(model) {
  const entry = { action: model.action, ...model.params };
  if (model.breakpoint) entry.breakpoint = true;
  if (model.save_as) entry.save_as = model.save_as;
  return entry;
}
function rawStepsToModel(rawList) {
  return (rawList || []).map(rawStepToModel);
}
function modelStepsToRaw(modelList) {
  return (modelList || []).map(modelStepToRaw);
}

// `opts.stepsArray`/`opts.onChange` let this same renderer serve both the
// top-level workflow (stepsArray = state.steps, onChange = renderSteps) and a
// nested control-flow branch (stepsArray = a locally-decoded model array,
// onChange = write it back into the parent step's raw params + renderSteps) -
// see renderNestedStepsField below. `opts.isNested` disables drag-reordering
// and the scope-aware "can't move above the scope step" logic, neither of
// which apply inside a branch.
function renderStepCard(step, index, actions, opts) {
  const stepsArray = opts.stepsArray || state.steps;
  const onChange = opts.onChange || renderSteps;
  const card = document.createElement("div");
  card.className = "step-card" + (opts.isScope ? " scope-card" : "");
  if (!opts.isNested) card.dataset.stepIndex = index + 1;

  // --- header: (drag handle,) breakpoint toggle, index, action select, move/delete buttons ---
  const head = document.createElement("div");
  head.className = "step-card-head";

  if (!opts.isScope && !opts.isNested) {
    const dragHandle = document.createElement("span");
    dragHandle.className = "drag-handle";
    dragHandle.title = "Ziehen zum Umsortieren";
    dragHandle.textContent = "⠿";
    dragHandle.draggable = true;
    dragHandle.addEventListener("dragstart", (e) => {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(index));
      e.dataTransfer.setDragImage(card, 20, 20);
      requestAnimationFrame(() => card.classList.add("dragging"));
    });
    dragHandle.addEventListener("dragend", () => {
      card.classList.remove("dragging");
    });
    head.appendChild(dragHandle);

    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const rect = card.getBoundingClientRect();
      const before = e.clientY - rect.top < rect.height / 2;
      card.classList.toggle("drag-over-top", before);
      card.classList.toggle("drag-over-bottom", !before);
    });
    card.addEventListener("dragleave", () => {
      card.classList.remove("drag-over-top", "drag-over-bottom");
    });
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      const wasBefore = card.classList.contains("drag-over-top");
      card.classList.remove("drag-over-top", "drag-over-bottom");
      const fromIndex = Number(e.dataTransfer.getData("text/plain"));
      if (Number.isNaN(fromIndex) || fromIndex === index) return;
      pushUndo();
      let toIndex = index;
      const [moved] = stepsArray.splice(fromIndex, 1);
      if (fromIndex < toIndex) toIndex -= 1;
      if (!wasBefore) toIndex += 1;
      stepsArray.splice(toIndex, 0, moved);
      onChange();
    });
  }

  const bpToggle = document.createElement("button");
  bpToggle.className = "bp-toggle" + (step.breakpoint ? " active" : "");
  bpToggle.title = step.breakpoint ? "Haltepunkt entfernen" : "Haltepunkt setzen";
  bpToggle.addEventListener("click", () => {
    pushUndo();
    step.breakpoint = !step.breakpoint;
    onChange();
  });
  head.appendChild(bpToggle);

  const idx = document.createElement("span");
  idx.className = "step-index";
  idx.textContent = index + 1;
  head.appendChild(idx);

  const actionSelect = document.createElement("select");
  for (const actionName of Object.keys(actions)) {
    const opt = document.createElement("option");
    opt.value = actionName;
    opt.textContent = actionName;
    if (actionName === step.action) opt.selected = true;
    actionSelect.appendChild(opt);
  }
  actionSelect.addEventListener("change", () => {
    pushUndo();
    step.action = actionSelect.value;
    step.params = {};
    onChange();
  });
  head.appendChild(actionSelect);

  if (!opts.isScope) {
    const actionsDiv = document.createElement("div");
    actionsDiv.className = "step-actions";

    const upBtn = document.createElement("button");
    upBtn.className = "btn-icon";
    upBtn.textContent = "↑";
    upBtn.title = "Nach oben";
    const topOffset = opts.isNested ? 0 : isScopeStep(stepsArray[0], 0) ? 1 : 0;
    upBtn.disabled = index <= topOffset;
    upBtn.addEventListener("click", () => {
      pushUndo();
      [stepsArray[index - 1], stepsArray[index]] = [stepsArray[index], stepsArray[index - 1]];
      onChange();
    });

    const downBtn = document.createElement("button");
    downBtn.className = "btn-icon";
    downBtn.textContent = "↓";
    downBtn.title = "Nach unten";
    downBtn.disabled = index === stepsArray.length - 1;
    downBtn.addEventListener("click", () => {
      pushUndo();
      [stepsArray[index + 1], stepsArray[index]] = [stepsArray[index], stepsArray[index + 1]];
      onChange();
    });

    const delBtn = document.createElement("button");
    delBtn.className = "btn-icon danger";
    delBtn.textContent = "✕";
    delBtn.title = "Löschen";
    delBtn.addEventListener("click", () => {
      pushUndo();
      stepsArray.splice(index, 1);
      onChange();
    });

    actionsDiv.append(upBtn, downBtn, delBtn);
    head.appendChild(actionsDiv);
  } else {
    const delBtn = document.createElement("button");
    delBtn.className = "btn-icon danger";
    delBtn.textContent = "✕";
    delBtn.title = "Scope entfernen";
    delBtn.style.marginLeft = "auto";
    delBtn.addEventListener("click", () => {
      pushUndo();
      stepsArray.splice(index, 1);
      onChange();
    });
    head.appendChild(delBtn);
  }
  card.appendChild(head);

  // --- dynamic parameter fields ---
  const fieldsDiv = document.createElement("div");
  fieldsDiv.className = "fields";
  const fieldDefs = actions[step.action] || [];

  for (const fieldDef of fieldDefs) {
    if (fieldDef.type === "steps") {
      fieldsDiv.appendChild(renderNestedStepsField(step, fieldDef.name, fieldDef.label, actions, opts));
      continue;
    }
    if (fieldDef.type === "cases") {
      fieldsDiv.appendChild(renderCasesField(step, fieldDef.name, fieldDef.label, actions, opts));
      continue;
    }

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
      if (opts.onFieldMutate) opts.onFieldMutate();
    });

    if (fieldDef.type === "checkbox") {
      wrap.append(input, label);
    } else if (fieldDef.type === "hotkey") {
      const recordBtn = document.createElement("button");
      recordBtn.type = "button";
      recordBtn.className = "btn-icon hotkey-record";
      recordBtn.textContent = "🎹";
      recordBtn.title = "Tastenkombination aufnehmen";
      recordBtn.addEventListener("click", () => recordHotkey(input, step, fieldDef.name));
      const inputRow = document.createElement("div");
      inputRow.className = "hotkey-row";
      inputRow.append(input, recordBtn);
      wrap.append(label, inputRow);
    } else {
      wrap.append(label, input);
    }
    fieldsDiv.appendChild(wrap);
  }

  // --- save_as: universal, not action-specific - stores this step's result
  // (e.g. get_text's return value) into a variable usable as {var.name} later ---
  const saveAsWrap = document.createElement("div");
  saveAsWrap.className = "field";
  const saveAsLabel = document.createElement("label");
  saveAsLabel.textContent = "Ergebnis speichern als (optional)";
  const saveAsInput = document.createElement("input");
  saveAsInput.type = "text";
  saveAsInput.placeholder = "Variablenname";
  saveAsInput.value = step.save_as || "";
  saveAsInput.addEventListener("input", () => {
    step.save_as = saveAsInput.value || "";
    if (opts.onFieldMutate) opts.onFieldMutate();
  });
  saveAsWrap.append(saveAsLabel, saveAsInput);
  fieldsDiv.appendChild(saveAsWrap);

  card.appendChild(fieldsDiv);

  // --- "Element wählen" button: only for actions that actually target an element ---
  const hasSelectorField = fieldDefs.some((f) => f.name === "selector");
  const hasDesktopTargetFields = fieldDefs.some((f) => ["control_type", "title", "auto_id"].includes(f.name));
  if (hasSelectorField || hasDesktopTargetFields) {
    const pickBtn = document.createElement("button");
    pickBtn.type = "button";
    pickBtn.className = "btn-pick";
    pickBtn.textContent = "🎯 Element auf dem Bildschirm wählen";
    pickBtn.addEventListener("click", () => {
      if (hasSelectorField) pickWebSelector(step);
      else pickDesktopSelector(step);
    });
    card.appendChild(pickBtn);
  }

  return card;
}

// Renders a nested, fully-visual (add/remove/reorder/edit) list of steps for a
// control-flow branch (if.then/else, for_each.steps, try.steps/catch) instead
// of the raw JSON textarea this used to be. `parentStep.params[fieldName]` is
// the wire-format raw array; edits are made against a locally-decoded "model"
// array and synced back on every change - see the onFieldMutate chaining
// comment on renderStepCard for why a sync is needed on every keystroke, not
// just on structural changes.
function renderNestedStepsField(parentStep, fieldName, label, actions, opts) {
  const wrap = document.createElement("div");
  wrap.className = "field-wide nested-steps-wrap";

  const heading = document.createElement("div");
  heading.className = "nested-steps-label";
  heading.textContent = label;
  wrap.appendChild(heading);

  const list = document.createElement("div");
  list.className = "nested-steps-list";

  const modelBranch = rawStepsToModel(parentStep.params[fieldName]);

  const sync = () => {
    parentStep.params[fieldName] = modelStepsToRaw(modelBranch);
    if (opts.onFieldMutate) opts.onFieldMutate();
  };

  const childOpts = {
    stepsArray: modelBranch,
    isNested: true,
    onChange: () => {
      sync();
      renderSteps();
    },
    onFieldMutate: sync,
  };

  if (modelBranch.length === 0) {
    const empty = document.createElement("div");
    empty.className = "nested-steps-empty";
    empty.textContent = "Keine Schritte";
    list.appendChild(empty);
  }
  modelBranch.forEach((childStep, i) => {
    list.appendChild(renderStepCard(childStep, i, actions, childOpts));
  });
  wrap.appendChild(list);

  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "btn btn-add btn-add-nested";
  addBtn.textContent = "+ Schritt hinzufügen";
  addBtn.addEventListener("click", () => {
    pushUndo();
    modelBranch.push(newStepFor(state.backend));
    sync();
    renderSteps();
  });
  wrap.appendChild(addBtn);

  return wrap;
}

// Same idea as renderNestedStepsField, but for switch's `cases` (a dict of
// case-value -> step list rather than a single list) - each case gets its own
// editable key plus a nested step list.
function renderCasesField(parentStep, fieldName, label, actions, opts) {
  const wrap = document.createElement("div");
  wrap.className = "field-wide nested-cases-wrap";

  const heading = document.createElement("div");
  heading.className = "nested-steps-label";
  heading.textContent = label;
  wrap.appendChild(heading);

  // Tracked as an ordered array (not just Object.entries() each render) so a
  // case key can be edited - including transiently duplicating another key -
  // without entries collapsing into each other mid-edit.
  const entries = Object.entries(parentStep.params[fieldName] || {}).map(([key, raw]) => ({
    key,
    modelBranch: rawStepsToModel(raw),
  }));

  const sync = () => {
    const cases = {};
    for (const entry of entries) {
      cases[entry.key || ""] = modelStepsToRaw(entry.modelBranch);
    }
    parentStep.params[fieldName] = cases;
    if (opts.onFieldMutate) opts.onFieldMutate();
  };

  for (const entry of entries) {
    const caseBox = document.createElement("div");
    caseBox.className = "case-box";

    const caseHead = document.createElement("div");
    caseHead.className = "case-box-head";

    const keyInput = document.createElement("input");
    keyInput.type = "text";
    keyInput.placeholder = "Wert (z.B. DE)";
    keyInput.value = entry.key;
    keyInput.addEventListener("focus", () => pushUndo());
    keyInput.addEventListener("input", () => {
      entry.key = keyInput.value;
      sync();
    });

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btn-icon danger";
    removeBtn.textContent = "✕";
    removeBtn.title = "Fall entfernen";
    removeBtn.addEventListener("click", () => {
      pushUndo();
      entries.splice(entries.indexOf(entry), 1);
      sync();
      renderSteps();
    });

    caseHead.append(keyInput, removeBtn);
    caseBox.appendChild(caseHead);

    const list = document.createElement("div");
    list.className = "nested-steps-list";
    const childOpts = {
      stepsArray: entry.modelBranch,
      isNested: true,
      onChange: () => {
        sync();
        renderSteps();
      },
      onFieldMutate: sync,
    };
    if (entry.modelBranch.length === 0) {
      const empty = document.createElement("div");
      empty.className = "nested-steps-empty";
      empty.textContent = "Keine Schritte";
      list.appendChild(empty);
    }
    entry.modelBranch.forEach((childStep, i) => {
      list.appendChild(renderStepCard(childStep, i, actions, childOpts));
    });
    caseBox.appendChild(list);

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "btn btn-add btn-add-nested";
    addBtn.textContent = "+ Schritt hinzufügen";
    addBtn.addEventListener("click", () => {
      pushUndo();
      entry.modelBranch.push(newStepFor(state.backend));
      sync();
      renderSteps();
    });
    caseBox.appendChild(addBtn);

    wrap.appendChild(caseBox);
  }

  const addCaseBtn = document.createElement("button");
  addCaseBtn.type = "button";
  addCaseBtn.className = "btn btn-pick";
  addCaseBtn.textContent = "+ Fall hinzufügen";
  addCaseBtn.addEventListener("click", () => {
    pushUndo();
    entries.push({ key: "", modelBranch: [] });
    sync();
    renderSteps();
  });
  wrap.appendChild(addCaseBtn);

  return wrap;
}

function renderRecordingControls() {
  const wrap = document.createElement("div");
  wrap.className = "record-controls";
  const btn = document.createElement("button");
  btn.className = "btn" + (currentRecordId ? " btn-recording" : "");
  btn.textContent = currentRecordId ? "⏹ Aufnahme stoppen" : "🔴 Aufnahme starten";
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

function renderSteps() {
  const container = el("steps");
  container.innerHTML = "";
  const actions = schema[state.backend] || {};

  if (state.steps.length && isScopeStep(state.steps[0], 0)) {
    const scopeWrap = document.createElement("div");
    scopeWrap.className = "scope-wrap";

    const scopeLabel = document.createElement("div");
    scopeLabel.className = "scope-label";
    scopeLabel.textContent = "🖥 Anwendungs-Scope";
    scopeWrap.appendChild(scopeLabel);

    scopeWrap.appendChild(renderStepCard(state.steps[0], 0, actions, { isScope: true }));
    scopeWrap.appendChild(renderRecordingControls());

    const seq = document.createElement("div");
    seq.className = "scope-sequence";
    const seqLabel = document.createElement("div");
    seqLabel.className = "sequence-label";
    seqLabel.textContent = "Sequenz";
    seq.appendChild(seqLabel);
    state.steps.slice(1).forEach((step, i) => {
      seq.appendChild(renderStepCard(step, i + 1, actions, {}));
    });
    scopeWrap.appendChild(seq);
    container.appendChild(scopeWrap);
  } else {
    state.steps.forEach((step, index) => {
      container.appendChild(renderStepCard(step, index, actions, {}));
    });
  }
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
      alert("Auswahl fehlgeschlagen: " + data.error);
    }
  } catch (err) {
    alert("Auswahl fehlgeschlagen: " + err);
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
      alert("Auswahl fehlgeschlagen: " + data.error);
    }
  } catch (err) {
    alert("Auswahl fehlgeschlagen: " + err);
  } finally {
    hidePickStatus(banner);
  }
}

async function startRecording() {
  const scope = findDesktopScope();
  if (!scope) {
    alert("Aufnahme benötigt einen Scope: der erste Schritt muss 'launch' oder 'connect' sein.");
    return;
  }
  const res = await fetch("/api/record/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scope),
  });
  const data = await res.json();
  if (!data.ok) {
    alert("Aufnahme konnte nicht gestartet werden: " + (data.error || res.status));
    return;
  }
  currentRecordId = data.record_id;
  renderSteps();

  const source = new EventSource(`/api/record/${currentRecordId}/stream`);
  recordingSource = source;
  source.addEventListener("step", (event) => {
    const s = JSON.parse(event.data);
    pushUndo();
    state.steps.push({ action: s.action, params: s.params || {}, breakpoint: false });
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
  return {
    name: el("wf-name").value || "workflow",
    backend: state.backend,
    steps: state.steps.map((s) => {
      const entry = { action: s.action, ...s.params };
      if (s.breakpoint) entry.breakpoint = true;
      if (s.save_as) entry.save_as = s.save_as;
      return entry;
    }),
  };
}

async function saveWorkflow() {
  const payload = currentWorkflowPayload();
  const res = await fetch(`/api/workflows/${encodeURIComponent(payload.name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    await loadWorkflowList();
    el("wf-load").value = payload.name;
  } else {
    const err = await res.json();
    alert("Speichern fehlgeschlagen: " + (err.error || res.status));
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
    const { index: stepIndex, action, variables } = JSON.parse(event.data);
    appendLog(`>> Haltepunkt bei Schritt ${stepIndex} (${action})`);
    el("log-status").textContent = `Angehalten bei Schritt ${stepIndex}`;
    el("log-status").className = "status-paused";
    el("btn-continue").classList.remove("hidden");
    clearPausedHighlight();
    const card = document.querySelector(`.step-card[data-step-index="${stepIndex}"]`);
    if (card) card.classList.add("paused-at");
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
    head.textContent = `📋 ${q.name}`;
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
    delBtn.addEventListener("click", async () => {
      if (!confirm(`Anmeldedaten "${name}" wirklich löschen?`)) return;
      await fetch(`/api/credentials/${encodeURIComponent(name)}`, { method: "DELETE" });
      renderCredentialsPanel();
    });
    row.append(label, delBtn);
    container.appendChild(row);
  }
}

async function addCredential() {
  const name = el("credential-name").value.trim();
  const value = el("credential-value").value;
  if (!name || !value) {
    alert("Bitte Name und Wert angeben.");
    return;
  }
  const res = await fetch("/api/credentials", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, value }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert("Speichern fehlgeschlagen: " + (data.error || res.status));
    return;
  }
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
    toggleBtn.textContent = s.enabled ? "⏸" : "▶";
    toggleBtn.title = s.enabled ? "Deaktivieren" : "Aktivieren";
    toggleBtn.addEventListener("click", async () => {
      await fetch(`/api/schedules/${s.id}/toggle`, { method: "POST" });
      renderSchedulesPanel();
    });

    const delBtn = document.createElement("button");
    delBtn.className = "btn-icon danger";
    delBtn.textContent = "✕";
    delBtn.title = "Löschen";
    delBtn.addEventListener("click", async () => {
      if (!confirm(`Zeitplan "${s.name}" wirklich löschen?`)) return;
      await fetch(`/api/schedules/${s.id}`, { method: "DELETE" });
      renderSchedulesPanel();
    });

    row.append(info, toggleBtn, delBtn);
    container.appendChild(row);
  }
}

async function addSchedule() {
  const name = el("schedule-name").value.trim();
  const cronExpr = el("schedule-cron").value.trim();
  if (!name || !cronExpr) {
    alert("Bitte Name und Cron-Ausdruck angeben.");
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
    alert("Planen fehlgeschlagen: " + (data.error || res.status));
    return;
  }
  el("schedule-name").value = "";
  el("schedule-cron").value = "";
  await renderSchedulesPanel();
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
    alert("Bitte Queue-Name und Excel-Datei angeben.");
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
    alert("Import fehlgeschlagen: " + (data.error || res.status));
    return;
  }
  fileInput.value = "";
  el("excel-queue-name").value = "";
  await renderQueuesPanel();
  await loadQueueNames();
}

function init() {
  el("btn-add-step").addEventListener("click", () => {
    pushUndo();
    state.steps.push(newStepFor(state.backend));
    renderSteps();
  });
  el("btn-save").addEventListener("click", saveWorkflow);
  el("btn-run").addEventListener("click", runWorkflow);
  el("btn-continue").addEventListener("click", continueRun);
  el("btn-stop").addEventListener("click", stopRun);
  el("btn-undo").addEventListener("click", undo);
  el("btn-logout").addEventListener("click", async () => {
    await fetch("/logout", { method: "POST" });
    location.href = "/";
  });
  el("btn-close-log").addEventListener("click", () => el("log-panel").classList.add("hidden"));

  el("btn-queues").addEventListener("click", () => {
    el("queues-panel").classList.remove("hidden");
    renderQueuesPanel();
  });
  el("btn-refresh-queues").addEventListener("click", renderQueuesPanel);
  el("btn-close-queues").addEventListener("click", () => el("queues-panel").classList.add("hidden"));
  el("btn-import-excel").addEventListener("click", importExcelToQueue);

  el("btn-credentials").addEventListener("click", () => {
    el("credentials-panel").classList.remove("hidden");
    renderCredentialsPanel();
  });
  el("btn-close-credentials").addEventListener("click", () => el("credentials-panel").classList.add("hidden"));
  el("btn-add-credential").addEventListener("click", addCredential);

  el("btn-schedules").addEventListener("click", () => {
    el("schedules-panel").classList.remove("hidden");
    renderSchedulesPanel();
  });
  el("btn-close-schedules").addEventListener("click", () => el("schedules-panel").classList.add("hidden"));
  el("btn-add-schedule").addEventListener("click", addSchedule);

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
    renderSteps();
  });

  el("wf-load").addEventListener("change", (e) => {
    if (e.target.value) loadWorkflow(e.target.value);
  });

  loadSchema().then(() => {
    state.steps = [newStepFor(state.backend)];
    renderSteps();
  });
  loadWorkflowList();
  loadQueueNames();
  updateUndoButton();
}

init();
