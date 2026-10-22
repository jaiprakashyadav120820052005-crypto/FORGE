function formatPrice(val) {
  if (val === null || val === undefined || isNaN(val)) return '—';
  return Number(val).toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
}

document.addEventListener('DOMContentLoaded', async () => {
  const state = {
    stage: 'home',
    questions: [],
    currentStep: 0,
    answers: {},
    activeProject: null,
    recommendations: [],
    catalog: [],
    starProjects: [],
    selectedStarProject: null,
    starsLoaded: false,
    activeBom: null,
    completedProjects: [],
    recommendationResponse: null,
    optimizerOpen: false,
    optimizerRole: null,
    optimizerCandidate: null,
    optimizerEvaluation: null,
    optimizerNotice: '',
    optimizerLoadId: 0,
    optimizerStagedFixes: new Set(),
    optimizerViewMode: 'cards',
    wiringData: null,
    componentTests: [],
    buildProgressKey: null,
    buildProgress: { preflight: {}, wires: {}, measuredRail: '', firstPowerCleared: false },
    buildRailRange: null
  };
  const stageHistory = [];

  // ── 1. Init & Sync State ──
  try {
    const s = await api.getState();
    state.currentStep = s.survey_step || 0;
    state.answers = s.survey_answers || {};
  } catch (err) {
    console.warn('Could not restore state from server:', err);
  }

  // Load Survey Questions & Catalog
  try {
    state.questions = await api.getQuestions();
    const cat = await api.getCatalog();
    state.catalog = cat.options || [];
    populateCatalogDropdown(state.catalog);
  } catch (err) {
    console.error('Initialization error:', err);
  }

  // Set initial view
  mountBuildResources();
  switchStage(state.stage);

  // ── Navigation ──
  document.querySelectorAll('[data-go]').forEach(button => {
    button.addEventListener('click', () => switchStage(button.dataset.go));
  });
  document.getElementById('btn-universal-back')?.addEventListener('click', goBack);
  document.getElementById('btn-back-to-questions')?.addEventListener('click', goBack);
  document.getElementById('btn-back-from-competition')?.addEventListener('click', goBack);
  document.getElementById('btn-skip-discovery')?.addEventListener('click', () => switchStage('bom'));

  function switchStage(stageName, { recordHistory = true, bomMode = 'setup' } = {}) {
    if (stageName === 'pick') stageName = 'discovery';
    const validStages = ['home', 'discovery', 'results', 'competition', 'stars', 'bom', 'build'];
    if (!validStages.includes(stageName)) {
      console.error(`Unknown FORGE view: ${stageName}`);
      return;
    }
    if (recordHistory && stageName !== state.stage) stageHistory.push(state.stage);
    state.stage = stageName;
    document.getElementById('btn-universal-back').hidden = stageName === 'home';
    document.getElementById('workflow-nav').hidden = stageName === 'home';
    document.querySelectorAll('#app-viewport > .view-panel').forEach(view => {
      view.style.display = 'none';
    });
    document.getElementById(`view-${stageName}`).style.display = 'block';

    if (stageName === 'discovery') renderSurveyStep(state.currentStep);
    if (stageName === 'competition') loadCompetitionEvents();
    if (stageName === 'stars') loadStarProjects();
    if (stageName === 'bom') {
      document.getElementById('bom-setup-panel').hidden = bomMode === 'guided';
      if (!state.activeBom) loadInitialBom();
    }
    if (stageName === 'build') renderBuildGuide();
    updateWorkflowNavigation();
  }

  const flowTargets = { recommend: 'discovery', source: 'bom', build: 'build' };
  document.querySelectorAll('[data-flow-go]').forEach(button => {
    button.addEventListener('click', () => {
      const target = flowTargets[button.dataset.flowGo];
      if (target) switchStage(target);
    });
  });

  document.getElementById('btn-build-back-to-source')?.addEventListener('click', () => switchStage('bom'));

  function updateWorkflowNavigation() {
    const current = ['discovery', 'results', 'competition', 'stars'].includes(state.stage)
      ? 'recommend'
      : state.stage === 'build' ? 'build' : 'source';
    document.querySelectorAll('[data-flow-go]').forEach(button => {
      const isCurrent = button.dataset.flowGo === current;
      button.classList.toggle('is-current', isCurrent);
      button.classList.toggle('is-complete', button.dataset.flowGo !== 'build' && Boolean(state.activeBom));
      if (button.dataset.flowGo === 'build') {
        button.disabled = !state.activeBom || !state.activeBom.parts_count;
        button.title = button.disabled ? 'Generate a BOM first' : 'Open the build and verification guide';
      }
      if (isCurrent) button.setAttribute('aria-current', 'step');
      else button.removeAttribute('aria-current');
    });
  }

  function mountBuildResources() {
    const target = document.getElementById('build-resource-mount');
    if (!target) return;
    ['specs-studio-card', 'wiring-studio-card'].forEach(id => {
      const resource = document.getElementById(id);
      if (resource) target.appendChild(resource);
    });
  }

  function syncBuildProgress() {
    const parts = Object.entries(state.activeBom?.parts || {})
      .filter(([, part]) => part && !part.none)
      .map(([role, part]) => `${role}:${part.id}:${part.qty || 1}`)
      .sort();
    const key = `forge-build:${state.activeBom?.project || ''}:${parts.join('|')}`;
    if (key === state.buildProgressKey) return;
    state.buildProgressKey = key;
    try {
      state.buildProgress = JSON.parse(localStorage.getItem(key) || 'null') || {
        preflight: {}, wires: {}, measuredRail: '', firstPowerCleared: false
      };
    } catch (err) {
      state.buildProgress = { preflight: {}, wires: {}, measuredRail: '', firstPowerCleared: false };
    }
  }

  function saveBuildProgress() {
    if (!state.buildProgressKey) return;
    try { localStorage.setItem(state.buildProgressKey, JSON.stringify(state.buildProgress)); }
    catch (err) { console.warn('Could not save local build checklist progress:', err); }
  }

  function renderBuildGuide() {
    const bom = state.activeBom;
    if (!bom) {
      updateWorkflowNavigation();
      return;
    }
    syncBuildProgress();

    const heading = document.getElementById('build-project-heading');
    if (heading) heading.textContent = `${bom.label || 'Project'} · Build & Verify`;

    const parts = Object.entries(bom.parts || {}).filter(([, part]) => part && !part.none);
    const controllerEntry = parts.find(([role, part]) => role === 'controller' || part.role === 'controller');
    const controller = controllerEntry?.[1];
    const controllerSpecs = controller?.specs || {};
    const logicVoltage = Number(controllerSpecs.logic_v);
    const sensors = parts.filter(([role, part]) => role === 'sensor' || part.role === 'sensor').map(([, part]) => part);
    const logicFailure = (bom.checks || []).some(check => check.id === 'logic_level' && check.status === 'FAIL');
    const signalRisk = (Number.isFinite(logicVoltage) && logicVoltage <= 3.3 && sensors.length > 0) || logicFailure;
    const hasHcSr04 = sensors.some(part => /hc.?sr04|ultrasonic/i.test(`${part.id} ${part.name}`));
    const signalWarning = document.getElementById('build-signal-warning');
    if (signalWarning) {
      signalWarning.hidden = !signalRisk;
      signalWarning.innerHTML = signalRisk
        ? `<b>Potential signal-voltage hazard.</b> ${escapeHtml(controller?.name || 'This controller')} uses ${Number.isFinite(logicVoltage) ? `${logicVoltage} V` : 'a low-voltage'} logic, but FORGE does not have verified per-pin output limits for the exact sensor variant. ${hasHcSr04 ? 'HC-SR04 ECHO is typically 5 V: do not connect it directly to an ESP32 GPIO; use a correctly rated divider or level shifter and verify the module datasheet.' : 'Check each sensor output-high voltage against the exact controller GPIO absolute maximum; add a divider or level shifter where required.'} The generated wiring is not proof that signal conditioning is present.`
        : '';
    }

    const preflight = [
      ['identity', 'Match every board and module marking to the BOM. Do not substitute a different variant without rechecking its ratings.'],
      ['datasheets', 'Check the exact controller GPIO voltage limits and each sensor output-high voltage in manufacturer documentation. FORGE values are typical team-entered data, not verified for your physical part.'],
      ['tools', 'Have a multimeter, insulated work surface, and the correct rated power supply available.'],
      ['power-off', 'Disconnect both battery and USB before wiring or changing any connection.'],
      ['polarity', 'Confirm battery, regulator, and electrolytic capacitor polarity against the printed + / - marks.'],
      ['shorts', 'Inspect for bridges and loose strands; with power off, check for unintended rail-to-ground shorts against the circuit reference.'],
      ['loads-off', 'Leave motor, servo, solenoid, and other high-current supplies disconnected during the first controller-and-sensor check.'],
      [signalRisk ? 'signal-protection' : 'signal-review', signalRisk
        ? 'I verified the exact sensor output level and installed a correctly rated divider or level shifter wherever a signal could exceed the controller GPIO limit.'
        : 'I checked the exact part pinout and confirmed no signal exceeds the connected input pin limit; unknown limits remain unresolved.']
    ];
    const preflightList = document.getElementById('build-preflight-list');
    preflightList.innerHTML = preflight.map(([id, text]) => `
      <label class="build-check-row">
        <input type="checkbox" data-build-check="${id}" ${state.buildProgress.preflight?.[id] ? 'checked' : ''}>
        <span>${escapeHtml(text)}</span>
      </label>`).join('');

    state.buildRailRange = Number.isFinite(logicVoltage) && logicVoltage > 0
      ? { min: Number((logicVoltage * 0.9).toFixed(2)), max: Number((logicVoltage * 1.1).toFixed(2)), nominal: logicVoltage }
      : null;
    const rangeText = document.getElementById('build-rail-range');
    if (rangeText) {
      rangeText.textContent = state.buildRailRange
        ? `Target logic rail: ${state.buildRailRange.nominal} V. Check the controller's 3V3/5V rail with a multimeter; the displayed ${state.buildRailRange.min}–${state.buildRailRange.max} V window is a ±10% coaching threshold, not a manufacturer specification. Also verify the board input range in its datasheet.`
        : 'No controller logic-rail target is available. Check the exact board datasheet; the first-power checkpoint remains blocked until a target is known.';
    }
    const railInput = document.getElementById('build-rail-reading');
    if (railInput) railInput.value = state.buildProgress.measuredRail || '';

    const connections = (state.wiringData?.connections || []).map((connection, index) => ({ connection, index }));
    const isHighPower = ({ connection }) => {
      const type = String(connection.type || '').toUpperCase();
      const text = `${connection.from_part || ''} ${connection.from_pin || ''} ${connection.to_part || ''} ${connection.to_pin || ''}`;
      return ['POWER', 'MOTOR'].includes(type) || /battery|motor|servo|solenoid|load/i.test(text);
    };
    const renderWireRows = rows => rows.map(({ connection, index }) => {
      const from = `${connection.from_part || 'Part'} · ${connection.from_pin || 'pin'}`;
      const to = `${connection.to_part || 'Controller'} · ${connection.to_pin || 'pin'}`;
      return `<li><label class="build-wire-row"><input type="checkbox" data-build-wire="${index}" ${state.buildProgress.wires?.[index] ? 'checked' : ''}><span><b>${escapeHtml(from)} → ${escapeHtml(to)}</b><small>${escapeHtml(connection.type || 'Connection')}${connection.note ? ` · ${escapeHtml(connection.note)}` : ''}</small></span></label></li>`;
    }).join('');
    const firstStage = connections.filter(row => !isHighPower(row));
    const secondStage = connections.filter(isHighPower);
    document.getElementById('build-wiring-stage-one').innerHTML = renderWireRows(firstStage) || '<li class="caption">No generated low-voltage connections are available. Do not treat this build as wiring-verified.</li>';
    document.getElementById('build-wiring-stage-two').innerHTML = renderWireRows(secondStage) || '<li class="caption">No motor or high-current connections were generated for this BOM.</li>';
    document.getElementById('build-stage-two').hidden = !state.buildProgress.firstPowerCleared;

    updateBuildGate(firstStage.length > 0);
    updateWorkflowNavigation();
  }

  function updateBuildGate(hasLowVoltageWiring) {
    const button = document.getElementById('btn-build-first-power');
    const status = document.getElementById('build-gate-status');
    if (!button || !status) return;
    const checks = [...document.querySelectorAll('#build-preflight-list [data-build-check]')];
    const firstWires = [...document.querySelectorAll('#build-wiring-stage-one [data-build-wire]')];
    const railValue = Number(document.getElementById('build-rail-reading')?.value);
    const range = state.buildRailRange;
    const railValid = range && Number.isFinite(railValue) && railValue >= range.min && railValue <= range.max;
    const wiringReady = Boolean(state.wiringData && firstWires.length > 0 && firstWires.every(input => input.checked));
    const ready = checks.length > 0 && checks.every(input => input.checked) && railValid && wiringReady;

    if (state.buildProgress.firstPowerCleared) {
      button.disabled = true;
      button.textContent = 'First-power checkpoint recorded';
      status.textContent = 'Checkpoint recorded. Disconnect power before Stage B; connect motor/high-current wiring only after the controller and sensors have been checked. This record is self-reported, not a safety certification.';
      const stageTwoRows = [...document.querySelectorAll('#build-wiring-stage-two [data-build-wire]')];
      const complete = stageTwoRows.length === 0 || stageTwoRows.every(input => input.checked);
      document.getElementById('build-stage-two-status').textContent = complete
        ? 'Stage B wiring checklist complete. Keep power disconnected until driver, load, and supply ratings are verified.'
        : `Stage B progress: ${stageTwoRows.filter(input => input.checked).length} of ${stageTwoRows.length} connections checked.`;
      return;
    }

    button.disabled = !ready;
    button.textContent = 'Complete first-power checks';
    if (!state.wiringData) status.textContent = 'Blocked: generated wiring is unavailable for this BOM.';
    else if (!range) status.textContent = 'Blocked: no controller logic-rail target is available; verify the exact board datasheet before first power.';
    else if (!railValid) status.textContent = `Blocked: measure the controller logic rail and enter a value from ${range.min} to ${range.max} V.`;
    else if (!checks.every(input => input.checked)) status.textContent = `Blocked: complete all ${checks.length} power-off and datasheet checks.`;
    else if (!wiringReady) status.textContent = 'Blocked: complete Stage A wiring one checked connection at a time.';
    else status.textContent = 'Checks recorded. This opens only the controller-and-logic first-power step; keep motor and high-current supplies disconnected.';
  }

  document.getElementById('view-build')?.addEventListener('change', event => {
    const target = event.target;
    if (target.matches('[data-build-check]')) {
      state.buildProgress.preflight[target.dataset.buildCheck] = target.checked;
      state.buildProgress.firstPowerCleared = false;
    } else if (target.matches('[data-build-wire]')) {
      state.buildProgress.wires[target.dataset.buildWire] = target.checked;
      if (target.closest('#build-wiring-stage-one')) state.buildProgress.firstPowerCleared = false;
    } else if (target.id === 'build-rail-reading') {
      state.buildProgress.measuredRail = target.value;
      state.buildProgress.firstPowerCleared = false;
    }
    saveBuildProgress();
    updateBuildGate();
  });

  document.getElementById('btn-build-first-power')?.addEventListener('click', () => {
    if (document.getElementById('btn-build-first-power').disabled) return;
    state.buildProgress.firstPowerCleared = true;
    saveBuildProgress();
    renderBuildGuide();
  });

  async function openGuidedBom(params) {
    const bom = await api.generateBom(params);
    renderBomView(bom);
    switchStage('bom', { bomMode: 'guided' });
  }

  function goBack() {
    const previousStage = stageHistory.pop() || 'home';
    switchStage(previousStage, { recordHistory: false });
  }

  document.getElementById('star-project-sort')?.addEventListener('change', renderStarProjects);

  async function loadStarProjects() {
    if (state.starsLoaded) return renderStarProjects();
    const count = document.getElementById('star-project-count');
    count.textContent = 'Loading curated builds…';
    try {
      const response = await api.getStarProjects();
      state.starProjects = response.projects || [];
      state.starsLoaded = true;
      renderStarProjects();
    } catch (err) {
      count.textContent = `Could not load star projects: ${err.message}`;
    }
  }

  function renderStarProjects() {
    const grid = document.getElementById('star-project-grid');
    const count = document.getElementById('star-project-count');
    if (!grid || !count) return;
    const projects = [...state.starProjects];
    const sortValue = document.getElementById('star-project-sort').value;
    const [sortField, sortDirection] = sortValue.split('_');
    if (sortField !== 'default') {
      const field = sortField === 'hours' ? 'build_hours' : 'cost_inr';
      const direction = sortDirection === 'desc' ? -1 : 1;
      projects.sort((left, right) => {
        const leftValue = left[field];
        const rightValue = right[field];
        if (leftValue == null && rightValue != null) return 1;
        if (rightValue == null && leftValue != null) return -1;
        if (leftValue != null && rightValue != null && leftValue !== rightValue) {
          return (leftValue - rightValue) * direction;
        }
        return left.name.localeCompare(right.name);
      });
    }
    grid.replaceChildren();
    count.textContent = `${projects.length} curated projects`;

    projects.forEach(project => {
      const card = document.createElement('article');
      card.className = 'card star-project-card';
      const heading = document.createElement('div');
      heading.className = 'star-project-heading';
      const title = document.createElement('h2');
      title.textContent = project.name;
      const tier = document.createElement('span');
      tier.className = 'star-project-tier';
      tier.textContent = '✦ STAR';
      heading.append(title, tier);

      const domain = document.createElement('p');
      domain.className = 'star-project-domain';
      domain.textContent = project.domain.replaceAll('_', ' ');
      const description = document.createElement('p');
      description.className = 'text-secondary star-project-description';
      description.textContent = project.description;
      const metrics = document.createElement('div');
      metrics.className = 'star-project-metrics';
      [
        `Difficulty ${project.difficulty}/5`,
        `${project.build_hours} build hours`,
        project.cost_inr == null
          ? 'Price estimate withheld pending verification'
          : `Rough estimate: ₹${formatPrice(project.cost_inr)} · not a quote`
      ].forEach(value => {
        const metric = document.createElement('span');
        metric.textContent = value;
        metrics.appendChild(metric);
      });

      const details = document.createElement('details');
      details.className = 'star-project-details';
      const summary = document.createElement('summary');
      summary.textContent = 'Skills, tools & build notes';
      const detailList = document.createElement('dl');
      [
        ['Skills', project.skills],
        ['Prerequisites', project.prerequisites],
        ['Tools', project.tools],
        ['Safety', project.safety],
        ['Parts availability', [`${project.parts_availability}/5`]]
      ].forEach(([label, values]) => {
        if (!values?.length) return;
        const term = document.createElement('dt');
        term.textContent = label;
        const definition = document.createElement('dd');
        definition.textContent = values.join(', ').replaceAll('_', ' ');
        detailList.append(term, definition);
      });
      details.append(summary, detailList);
      const selectButton = document.createElement('button');
      selectButton.className = 'btn star-project-select';
      selectButton.type = 'button';
      selectButton.textContent = state.selectedStarProject?.key === project.key
        ? 'Selected'
        : 'Select project';
      selectButton.setAttribute('aria-pressed', String(state.selectedStarProject?.key === project.key));
      selectButton.addEventListener('click', () => {
        state.selectedStarProject = project;
        renderStarProjects();
      });
      card.classList.toggle('star-project-card-selected', state.selectedStarProject?.key === project.key);
      card.append(heading, domain, description, metrics, details, selectButton);
      grid.appendChild(card);
    });
    renderSelectedStarProject();
  }

  function renderSelectedStarProject() {
    const container = document.getElementById('star-project-selection');
    const project = state.selectedStarProject;
    if (!container) return;
    container.hidden = !project;
    if (!project) {
      container.replaceChildren();
      return;
    }

    const title = document.createElement('h3');
    title.textContent = `Selected project: ${project.name}`;
    const description = document.createElement('p');
    description.className = 'text-secondary';
    description.textContent = project.description;
    const action = document.createElement('button');
    action.className = 'btn primary';
    action.type = 'button';
    if (project.bom_supported) {
      action.textContent = 'Build this project';
      action.addEventListener('click', async () => {
        action.disabled = true;
        try {
          await openGuidedBom({
            project: project.key,
            budget: project.cost_inr ?? 3500
          });
        } catch (err) {
          action.disabled = false;
          document.getElementById('star-project-selection-status').textContent =
            `Could not open this build: ${err.message}`;
        }
      });
    } else {
      action.textContent = 'BOM not available yet';
      action.disabled = true;
      action.title = 'This curated project has no catalog bill of materials yet.';
    }

    const status = document.createElement('p');
    status.id = 'star-project-selection-status';
    status.className = 'caption';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    status.textContent = project.bom_supported
      ? 'This project has a catalog BOM ready to open.'
      : 'Project selected. A catalog BOM is not available for this build yet.';
    container.replaceChildren(title, description, action, status);
  }

  document.getElementById('btn-toggle-optimizer')?.addEventListener('click', async () => {
    state.optimizerOpen = !state.optimizerOpen;
    const content = document.getElementById('optimizer-content');
    const button = document.getElementById('btn-toggle-optimizer');
    content.hidden = !state.optimizerOpen;
    button.setAttribute('aria-expanded', String(state.optimizerOpen));
    button.textContent = state.optimizerOpen ? 'Close optimizer' : 'Open optimizer';
    if (state.optimizerOpen) {
      renderOptimizerRoles();
      await loadOptimizerCandidates();
    }
  });

  document.getElementById('optimizer-role-select')?.addEventListener('change', async event => {
    state.optimizerRole = event.target.value;
    state.optimizerEvaluation = null;
    state.optimizerNotice = '';
    state.optimizerStagedFixes.clear();
    document.getElementById('optimizer-result').hidden = true;
    document.getElementById('optimizer-actions').hidden = true;
    await loadOptimizerCandidates();
  });
  document.getElementById('optimizer-part-select')?.addEventListener('change', event => {
    state.optimizerCandidate = event.target.value;
    state.optimizerEvaluation = null;
    state.optimizerNotice = '';
    state.optimizerStagedFixes.clear();
    document.getElementById('optimizer-result').hidden = true;
    document.getElementById('optimizer-actions').hidden = true;
  });

  document.getElementById('btn-evaluate-swap')?.addEventListener('click', evaluateOptimizerSwap);
  document.getElementById('btn-keep-swap')?.addEventListener('click', async () => {
    if (!state.optimizerRole || !state.optimizerCandidate || !state.optimizerEvaluation?.summary?.ready) return;
    const button = document.getElementById('btn-keep-swap');
    button.disabled = true;
    try {
      const stagedList = Array.from(state.optimizerStagedFixes || []);
      await api.applySwap(state.optimizerRole, state.optimizerCandidate, stagedList);
      state.optimizerNotice = 'Replacement applied. The BOM, bill, wiring, and compatibility data now use the updated build.';
      state.optimizerStagedFixes.clear();
      renderBomView(await api.getBom());
    } catch (err) {
      state.optimizerNotice = '';
      document.getElementById('optimizer-message').textContent = `Could not apply replacement: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById('btn-reset-swaps')?.addEventListener('click', async () => {
    const button = document.getElementById('btn-reset-swaps');
    button.disabled = true;
    try {
      await api.resetSwaps();
      state.optimizerNotice = 'All optimizer replacements were undone.';
      state.optimizerStagedFixes.clear();
      renderBomView(await api.getBom());
    } catch (err) {
      state.optimizerNotice = '';
      document.getElementById('optimizer-message').textContent = `Could not undo replacements: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  });

  function renderOptimizerRoles() {
    const selector = document.getElementById('optimizer-role-select');
    const parts = Object.entries(state.activeBom?.parts || {}).filter(([, part]) => part && !part.none);
    selector.replaceChildren();
    parts.forEach(([key, part]) => {
      const option = document.createElement('option');
      option.value = key;
      option.textContent = `${part.role || key}: ${part.name}`;
      selector.appendChild(option);
    });
    if (!parts.some(([key]) => key === state.optimizerRole)) {
      state.optimizerRole = parts[0]?.[0] || null;
    }
    selector.value = state.optimizerRole || '';
    selector.disabled = parts.length === 0;
    if (!parts.length) {
      document.getElementById('optimizer-message').textContent = 'This BOM has no replaceable components.';
    }
  }

  async function loadOptimizerCandidates() {
    const selector = document.getElementById('optimizer-part-select');
    const message = document.getElementById('optimizer-message');
    const evaluateButton = document.getElementById('btn-evaluate-swap');
    const role = state.optimizerRole;
    const current = state.activeBom?.parts?.[role];
    const requestId = ++state.optimizerLoadId;
    selector.replaceChildren();
    state.optimizerCandidate = null;
    evaluateButton.disabled = true;
    if (!role || !current) {
      message.textContent = 'Choose a component from the active BOM.';
      return;
    }
    message.textContent = state.optimizerNotice || 'Loading replacement options…';
    try {
      const candidates = await api.getOptimizerCandidates(role);
      if (requestId !== state.optimizerLoadId) return;
      candidates.filter(candidate => candidate.id !== current.id).forEach(candidate => {
        const option = document.createElement('option');
        option.value = candidate.id;
        option.textContent = `${candidate.name}${candidate.price == null ? ' · price unknown' : ` · ₹${formatPrice(candidate.price)}`}`;
        selector.appendChild(option);
      });
      if (!selector.options.length) {
        message.textContent = 'No alternative components are available for this role yet.';
        return;
      }
      state.optimizerCandidate = selector.value;
      evaluateButton.disabled = false;
      message.textContent = state.optimizerNotice || 'Select an alternative and compare it with the current configuration.';
    } catch (err) {
      if (requestId === state.optimizerLoadId) message.textContent = `Could not load replacements: ${err.message}`;
    }
  }

  async function evaluateOptimizerSwap() {
    if (!state.optimizerRole || !state.optimizerCandidate) return;
    const button = document.getElementById('btn-evaluate-swap');
    button.disabled = true;
    state.optimizerNotice = '';
    document.getElementById('optimizer-message').textContent = 'Calculating component and system impact…';
    try {
      const stagedList = Array.from(state.optimizerStagedFixes || []);
      state.optimizerEvaluation = await api.evaluateSwap(state.optimizerRole, state.optimizerCandidate, stagedList);
      renderOptimizerEvaluation(state.optimizerEvaluation);
    } catch (err) {
      document.getElementById('optimizer-message').textContent = `Could not compare this replacement: ${err.message}`;
      document.getElementById('optimizer-result').hidden = true;
      document.getElementById('optimizer-actions').hidden = true;
    } finally {
      button.disabled = false;
    }
  }

  function renderOptimizerEvaluation(payload) {
    const result = document.getElementById('optimizer-result');
    const actions = document.getElementById('optimizer-actions');
    const evaluation = payload.evaluation || {};
    const summary = payload.summary || {};
    result.replaceChildren();

    // 1. Comparison Header
    const currentPart = state.activeBom?.parts?.[state.optimizerRole];
    const currentName = currentPart?.name || state.optimizerRole;
    const candSelect = document.getElementById('optimizer-part-select');
    const candName = candSelect?.selectedOptions?.[0]?.text?.split('·')?.[0]?.trim() || state.optimizerCandidate;
    const headerDiv = document.createElement('div');
    headerDiv.className = 'optimizer-eval-header';
    headerDiv.innerHTML = `<h4>${escapeHtml(currentName)} &rarr; ${escapeHtml(candName)}</h4>`;
    result.appendChild(headerDiv);

    // 2. Status Badge & Counts Row
    const badgeRow = document.createElement('div');
    badgeRow.className = 'optimizer-badge-row';
    const isReady = Boolean(summary.ready);
    const badgeStatus = !isReady ? 'FAIL' : (summary.warn > 0 || summary.unknown > 0 ? 'WARN' : 'PASS');
    const badgeLabel = !isReady ? 'INCOMPATIBLE' : (summary.warn > 0 || summary.unknown > 0 ? 'CAUTION' : 'COMPATIBLE');
    badgeRow.innerHTML = `
      <span class="b ${badgeStatus}">${badgeLabel}</span>
      <span class="caption text-muted" style="margin-left: 8px;">${summary.fail || 0} fail &middot; ${summary.warn || 0} warning &middot; ${summary.unknown || 0} unknown &middot; ${summary.passed || 0} pass</span>
    `;
    result.appendChild(badgeRow);

    // 3. Status Alert Banner
    const status = document.createElement('div');
    status.className = `alert ${isReady ? 'success' : 'error'}`;
    status.textContent = isReady
      ? `Proposed build has no failed compatibility rules (${summary.warn || 0} warnings, ${summary.unknown || 0} unknown).`
      : 'This is NOT a ready to build configuration: a compatibility rule fails. You can still inspect the numbers below.';
    result.appendChild(status);

    // 4. Failure Details & 1-Click Auto-Resolve Actions
    const failedRules = (evaluation.compat_proposed || []).filter(r => r.status === 'FAIL');
    const resolvedRules = evaluation.resolved_failures || [];

    if (failedRules.length > 0 || resolvedRules.length > 0) {
      const details = document.createElement('details');
      details.className = 'optimizer-failure-details';
      details.open = true;

      const summaryElem = document.createElement('summary');
      summaryElem.className = 'optimizer-failure-summary';
      summaryElem.textContent = 'Why did this build fail? (Click to view failure details)';
      details.appendChild(summaryElem);

      failedRules.forEach(rule => {
        const item = document.createElement('div');
        item.className = 'optimizer-failure-item';
        item.innerHTML = `
          <div class="failure-title">❌ <b>${escapeHtml(rule.title)}</b> ( <span class="text-secondary">${escapeHtml(rule.components || '')}</span> )</div>
          <div class="failure-reason"><b>Reason:</b> ${escapeHtml(rule.explanation || 'Rule check failed.')}</div>
          ${rule.action ? `<div class="failure-fix">💡 <b>Suggested Fix:</b> ${escapeHtml(rule.action)}</div>` : ''}
        `;

        if (rule.auto_fixes && rule.auto_fixes.length > 0) {
          const autoResolveDiv = document.createElement('div');
          autoResolveDiv.className = 'auto-resolve-section';
          autoResolveDiv.innerHTML = `<div class="auto-resolve-heading">🛠️ <b>1-Click Auto-Resolve Actions:</b></div>`;
          const btnRow = document.createElement('div');
          btnRow.className = 'auto-resolve-buttons';

          rule.auto_fixes.forEach(fix => {
            const btn = document.createElement('button');
            btn.type = 'button';
            const isStaged = state.optimizerStagedFixes.has(fix.id);
            btn.className = `btn auto-resolve-btn ${isStaged ? 'staged' : ''}`;
            btn.innerHTML = `${isStaged ? '✓ ' + escapeHtml(fix.label) + ' (Staged)' : (fix.icon ? fix.icon + ' ' : '') + escapeHtml(fix.label)}`;
            btn.title = fix.description || '';
            btn.addEventListener('click', async (e) => {
              e.preventDefault();
              e.stopPropagation();
              if (state.optimizerStagedFixes.has(fix.id)) {
                state.optimizerStagedFixes.delete(fix.id);
              } else {
                state.optimizerStagedFixes.add(fix.id);
              }
              await evaluateOptimizerSwap();
            });
            btnRow.appendChild(btn);
          });
          autoResolveDiv.appendChild(btnRow);
          item.appendChild(autoResolveDiv);
        }
        details.appendChild(item);
      });

      resolvedRules.forEach(rule => {
        const item = document.createElement('div');
        item.className = 'optimizer-failure-item resolved';
        item.innerHTML = `
          <div class="failure-title">✅ <b>${escapeHtml(rule.title)}</b> ( <span class="text-secondary">${escapeHtml(rule.components || '')}</span> ) <span class="b PASS">Resolved</span></div>
          <div class="failure-reason"><b>Status:</b> Issue resolved by staged auto-fix.</div>
        `;

        if (rule.auto_fixes && rule.auto_fixes.length > 0) {
          const autoResolveDiv = document.createElement('div');
          autoResolveDiv.className = 'auto-resolve-section';
          autoResolveDiv.innerHTML = `<div class="auto-resolve-heading">🛠️ <b>1-Click Auto-Resolve Actions:</b></div>`;
          const btnRow = document.createElement('div');
          btnRow.className = 'auto-resolve-buttons';

          rule.auto_fixes.forEach(fix => {
            const btn = document.createElement('button');
            btn.type = 'button';
            const isStaged = state.optimizerStagedFixes.has(fix.id);
            btn.className = `btn auto-resolve-btn ${isStaged ? 'staged' : ''}`;
            btn.innerHTML = `${isStaged ? '✓ ' + escapeHtml(fix.label) + ' (Staged)' : (fix.icon ? fix.icon + ' ' : '') + escapeHtml(fix.label)}`;
            btn.title = fix.description || '';
            btn.addEventListener('click', async (e) => {
              e.preventDefault();
              e.stopPropagation();
              if (state.optimizerStagedFixes.has(fix.id)) {
                state.optimizerStagedFixes.delete(fix.id);
              } else {
                state.optimizerStagedFixes.add(fix.id);
              }
              await evaluateOptimizerSwap();
            });
            btnRow.appendChild(btn);
          });
          autoResolveDiv.appendChild(btnRow);
          item.appendChild(autoResolveDiv);
        }
        details.appendChild(item);
      });

      result.appendChild(details);
    }

    // 5. Catalog Corrections
    if (evaluation.catalog_corrections && evaluation.catalog_corrections.length > 0) {
      evaluation.catalog_corrections.forEach(c => {
        const corrDiv = document.createElement('div');
        corrDiv.className = 'catalog-correction-box';
        corrDiv.innerHTML = `<span>${escapeHtml(c.message)}</span>`;
        result.appendChild(corrDiv);
      });
    }

    // 6. What Changes (Chips)
    if (evaluation.chips?.length) {
      const whatChangesTitle = document.createElement('div');
      whatChangesTitle.className = 'optimizer-section-heading';
      whatChangesTitle.innerHTML = '<b>What changes</b>';
      result.appendChild(whatChangesTitle);

      const chips = document.createElement('div');
      chips.className = 'optimizer-chips';
      evaluation.chips.forEach(item => {
        const chip = document.createElement('span');
        chip.className = `optimizer-chip ${item.tone || ''}`;
        chip.textContent = item.text;
        chips.appendChild(chip);
      });
      result.appendChild(chips);
    }

    if (evaluation.data_gaps?.length) {
      const gapDiv = document.createElement('div');
      gapDiv.className = 'caption text-muted';
      gapDiv.style.margin = '0.5rem 0 0.8rem';
      gapDiv.textContent = `Some figures for ${evaluation.data_gaps.join(', ')} are not available yet, so those rows show what is missing.`;
      result.appendChild(gapDiv);
    }

    // 7. System Impact & Metrics (Cards & Table Toggle)
    const labelMap = {
      improved: 'Improved',
      reduced: 'Worse',
      unchanged: 'Unchanged',
      incompatible: 'Incompatible',
      caution: 'Caution',
      unknown: 'Insufficient data',
      not_affected: 'Not affected'
    };

    const qualityMap = {
      typical: 'Typical data',
      estimated: 'Estimated',
      rules: 'Rule check',
      unavailable: 'No data yet',
      unverified: 'Unverified',
      verified: 'Verified live'
    };

    const formatMetricVal = (row, val) => {
      if (val === null || val === undefined) return '—';
      if (row.key === 'bluetooth' || row.key === 'wifi') return val ? 'Yes' : 'No';
      if (row.key === 'cost' && typeof val === 'number') return `₹${formatPrice(val)}`;
      if (typeof val === 'number') {
        const numStr = Number(val).toLocaleString(undefined, { maximumFractionDigits: 2 });
        return row.unit ? `${numStr} ${row.unit}` : numStr;
      }
      return String(val);
    };

    const formatDiffVal = (row) => {
      if (row.diff === null || row.diff === undefined) return '—';
      if (row.key === 'bluetooth' || row.key === 'wifi') {
        if (row.diff === 0) return 'No change';
        return row.diff > 0 ? '+ Added' : '- Removed';
      }
      let diffStr = '';
      if (row.key === 'cost') {
        const sign = row.diff > 0 ? '+' : (row.diff < 0 ? '-' : '');
        diffStr = `${sign}₹${formatPrice(Math.abs(row.diff))}`;
      } else {
        const sign = row.diff > 0 ? '+' : '';
        const numStr = Number(row.diff).toLocaleString(undefined, { maximumFractionDigits: 2 });
        diffStr = `${sign}${numStr}${row.unit ? ` ${row.unit}` : ''}`;
      }
      if (row.pct != null && row.pct !== undefined) {
        const pctSign = row.pct > 0 ? '+' : '';
        diffStr += ` (${pctSign}${Number(row.pct).toFixed(1)}%)`;
      }
      return diffStr;
    };

    const sectionHeader = document.createElement('div');
    sectionHeader.className = 'optimizer-section-header';
    sectionHeader.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px;">
        <h5 style="margin: 0; font-weight: 700; color: var(--text-primary);">System Impact &amp; Metrics</h5>
        <span class="caption text-muted">(${evaluation.rows?.length || 0} metrics compared)</span>
      </div>
      <div class="optimizer-view-toggle">
        <button type="button" class="view-toggle-btn ${state.optimizerViewMode === 'cards' ? 'active' : ''}" data-mode="cards">🗂️ Cards</button>
        <button type="button" class="view-toggle-btn ${state.optimizerViewMode === 'table' ? 'active' : ''}" data-mode="table">📊 Table</button>
      </div>
    `;
    result.appendChild(sectionHeader);

    // Cards Container
    const cardsContainer = document.createElement('div');
    cardsContainer.className = 'optimizer-metric-cards';
    cardsContainer.style.display = state.optimizerViewMode === 'cards' ? 'flex' : 'none';

    // Table Container
    const tableContainer = document.createElement('div');
    tableContainer.className = 'optimizer-table-container';
    tableContainer.style.display = state.optimizerViewMode === 'table' ? 'block' : 'none';

    const table = document.createElement('table');
    table.className = 'fg-t optimizer-results-table';
    table.innerHTML = '<thead><tr><th>Impact</th><th>Current</th><th>Proposed</th><th>Difference</th><th>Status</th></tr></thead>';
    const tbody = document.createElement('tbody');

    (evaluation.rows || []).forEach(row => {
      const statusClass = row.status || 'unchanged';
      const statusText = labelMap[statusClass] || statusClass.toUpperCase();
      const origText = formatMetricVal(row, row.original);
      const propText = formatMetricVal(row, row.proposed);
      const diffText = formatDiffVal(row);

      let diffTone = 'diff-neutral';
      if (statusClass === 'improved') diffTone = 'diff-good';
      else if (statusClass === 'reduced') diffTone = 'diff-bad';

      const qualityText = qualityMap[row.quality] || (row.quality && row.quality !== 'n/a' ? row.quality : '');

      // Build Card
      const card = document.createElement('div');
      card.className = `optimizer-metric-card ${statusClass}-card`;
      card.innerHTML = `
        <div class="metric-card-top">
          <div class="metric-card-title">
            <span>${escapeHtml(row.metric)}</span>
          </div>
          <span class="pill ${statusClass}">${escapeHtml(statusText)}</span>
        </div>
        <div class="metric-card-grid">
          <div class="metric-card-col">
            <span class="metric-card-label">Original</span>
            <span class="metric-card-val">${escapeHtml(origText)}</span>
          </div>
          <div class="metric-card-col">
            <span class="metric-card-label">Proposed</span>
            <span class="metric-card-val">${escapeHtml(propText)}</span>
          </div>
          <div class="metric-card-col">
            <span class="metric-card-label">Difference</span>
            <span class="metric-card-val metric-diff ${diffTone}">${escapeHtml(diffText)}</span>
          </div>
        </div>
        <div class="metric-card-footer">
          <span class="metric-card-reason">${escapeHtml(row.reason || '')}</span>
          ${qualityText ? `<span class="q-badge" title="Data confidence">${escapeHtml(qualityText)}</span>` : ''}
        </div>
      `;
      cardsContainer.appendChild(card);

      // Build Table Row
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><b>${escapeHtml(row.metric)}</b></td>
        <td>${escapeHtml(origText)}</td>
        <td>${escapeHtml(propText)}</td>
        <td><span class="metric-diff ${diffTone}">${escapeHtml(diffText)}</span></td>
        <td><span class="pill ${statusClass}">${escapeHtml(statusText)}</span></td>
      `;
      tr.title = `${row.reason || ''}${qualityText ? ` · ${qualityText}` : ''}`;
      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    tableContainer.appendChild(table);

    result.appendChild(cardsContainer);
    result.appendChild(tableContainer);

    // Setup View Mode Toggle Listener
    sectionHeader.querySelectorAll('.view-toggle-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const mode = btn.dataset.mode;
        state.optimizerViewMode = mode;
        sectionHeader.querySelectorAll('.view-toggle-btn').forEach(b => {
          b.classList.toggle('active', b.dataset.mode === mode);
        });
        cardsContainer.style.display = mode === 'cards' ? 'flex' : 'none';
        tableContainer.style.display = mode === 'table' ? 'block' : 'none';
      });
    });

    // 8. Explanation of Trade-offs (Accordion)
    if (evaluation.plain_explanation) {
      const tradeoffsDetails = document.createElement('details');
      tradeoffsDetails.className = 'optimizer-tradeoffs-details';
      tradeoffsDetails.open = true;
      tradeoffsDetails.innerHTML = `
        <summary class="optimizer-tradeoffs-summary">💡 Explanation of Trade-offs</summary>
        <div class="optimizer-tradeoffs-content">
          <p style="margin: 0; white-space: pre-line; line-height: 1.6; color: var(--text-secondary);">${escapeHtml(evaluation.plain_explanation)}</p>
        </div>
      `;
      result.appendChild(tradeoffsDetails);
    }

    // 9. All Rules List (collapsible expander)
    if (evaluation.compat_proposed?.length) {
      const rulesExpander = document.createElement('details');
      rulesExpander.className = 'optimizer-all-rules-details';
      const rulesSummary = document.createElement('summary');
      rulesSummary.className = 'caption text-muted';
      rulesSummary.style.cursor = 'pointer';
      rulesSummary.style.fontWeight = '600';
      rulesSummary.style.padding = '4px 0';
      rulesSummary.textContent = `📋 Compatibility Rules & Datasheet Checks (${evaluation.compat_proposed.length} verified)`;
      rulesExpander.appendChild(rulesSummary);

      const rules = document.createElement('div');
      rules.className = 'optimizer-rule-list';
      evaluation.compat_proposed.forEach(rule => {
        const item = document.createElement('div');
        item.className = `optimizer-rule-item ${rule.status || ''}`;
        
        const badge = document.createElement('span');
        badge.className = `pill ${rule.status}`;
        badge.textContent = rule.status;
        item.appendChild(badge);

        const textSpan = document.createElement('span');
        textSpan.style.flex = '1';
        textSpan.innerHTML = `<b>${escapeHtml(rule.title)}</b>${rule.components ? ` <span class="text-secondary">(${escapeHtml(rule.components)})</span>` : ''} &middot; ${escapeHtml(rule.explanation || '')}${rule.action ? ` <b style="color: var(--primary-orange);">Do this:</b> ${escapeHtml(rule.action)}` : ''}`;
        item.appendChild(textSpan);

        rules.appendChild(item);
      });
      rulesExpander.appendChild(rules);
      result.appendChild(rulesExpander);
    }

    result.hidden = false;
    actions.hidden = false;
    document.getElementById('btn-keep-swap').disabled = !summary.ready;
    document.getElementById('optimizer-message').textContent = summary.ready
      ? 'Numbers are calculated from FORGE’s catalog and available component profiles; unknown values are shown as unavailable.'
      : 'This replacement is not ready to apply because at least one compatibility rule fails.';
  }

  // ── 2. Survey Engine ──
  function renderSurveyStep(step) {
    if (!state.questions || state.questions.length === 0) return;
    state.currentStep = Math.max(0, Math.min(step, state.questions.length - 1));
    const q = state.questions[state.currentStep];

    document.getElementById('survey-step-indicator').textContent = `QUESTION ${String(state.currentStep + 1).padStart(2, '0')} / ${String(state.questions.length).padStart(2, '0')}`;
    const pct = ((state.currentStep + 1) / state.questions.length) * 100;
    document.getElementById('survey-progress-bar').style.width = `${pct}%`;
    document.getElementById('survey-question-text').textContent = q.question;

    const container = document.getElementById('survey-input-container');
    container.innerHTML = '';

    const currentVal = state.answers[q.key] || (q.multi ? [] : '');

    // Options Pills
    if (q.options && q.options.length > 0) {
      const pGroup = document.createElement('div');
      pGroup.className = 'pills-group';
      q.options.forEach(opt => {
        const pBtn = document.createElement('div');
        const isSelected = q.multi ? (currentVal || []).includes(opt) : currentVal === opt;
        pBtn.className = `pill-opt ${isSelected ? 'selected' : ''}`;
        pBtn.textContent = opt;
        pBtn.addEventListener('click', () => {
          if (q.multi) {
            let list = Array.isArray(state.answers[q.key]) ? [...state.answers[q.key]] : [];
            if (list.includes(opt)) list = list.filter(x => x !== opt);
            else list.push(opt);
            state.answers[q.key] = list;
          } else {
            state.answers[q.key] = opt;
          }
          api.saveSurveyAnswers(state.answers, state.currentStep);
          renderSurveyStep(state.currentStep);
        });
        pGroup.appendChild(pBtn);
      });
      container.appendChild(pGroup);
    }

    // Specific text/number inputs
    if (q.key === 'career') {
      const inp = document.createElement('input');
      inp.type = 'text';
      inp.placeholder = 'Dream job, destination company, or target industry';
      inp.value = state.answers['dream_job'] || '';
      inp.addEventListener('change', () => {
        state.answers['dream_job'] = inp.value;
        api.saveSurveyAnswers(state.answers, state.currentStep);
      });
      container.appendChild(inp);
    } else if (q.key === 'budget') {
      const inp = document.createElement('input');
      inp.type = 'number';
      inp.placeholder = 'Maximum budget in ₹ (0 = not specified)';
      inp.value = state.answers['budget_inr'] || '';
      inp.addEventListener('change', () => {
        state.answers['budget_inr'] = parseInt(inp.value) || null;
        api.saveSurveyAnswers(state.answers, state.currentStep);
      });
      container.appendChild(inp);
    } else if (q.key === 'schedule') {
      const schedule = state.answers.schedule || {};
      const scheduleInputs = document.createElement('div');
      scheduleInputs.className = 'schedule-inputs';

      const hoursLabel = document.createElement('label');
      hoursLabel.textContent = 'Hours available each week';
      const hoursInput = document.createElement('input');
      hoursInput.type = 'number';
      hoursInput.min = '1';
      hoursInput.max = '80';
      hoursInput.step = '1';
      hoursInput.placeholder = 'e.g. 6';
      hoursInput.value = schedule.hours_per_week || '';
      hoursInput.addEventListener('change', () => {
        state.answers.schedule = {
          ...state.answers.schedule,
          hours_per_week: Number(hoursInput.value) || null
        };
        state.answers.hours_per_week = Number(hoursInput.value) || null;
        api.saveSurveyAnswers(state.answers, state.currentStep);
      });
      hoursLabel.appendChild(hoursInput);

      const deadlineLabel = document.createElement('label');
      deadlineLabel.textContent = 'Target completion';
      const deadlineSelect = document.createElement('select');
      [
        ['', 'No fixed deadline'],
        ['2', 'Within 2 weeks'],
        ['4', 'Within 1 month'],
        ['8', 'Within 2 months'],
        ['12', 'Within 3 months'],
        ['24', 'Within 6 months']
      ].forEach(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        deadlineSelect.appendChild(option);
      });
      deadlineSelect.value = schedule.deadline_weeks || '';
      deadlineSelect.addEventListener('change', () => {
        state.answers.schedule = {
          ...state.answers.schedule,
          deadline_weeks: Number(deadlineSelect.value) || null
        };
        state.answers.deadline_weeks = Number(deadlineSelect.value) || null;
        api.saveSurveyAnswers(state.answers, state.currentStep);
      });
      deadlineLabel.appendChild(deadlineSelect);
      scheduleInputs.append(hoursLabel, deadlineLabel);
      container.appendChild(scheduleInputs);
    }

    // Buttons
    document.getElementById('survey-prev-btn').disabled = (state.currentStep === 0);
    const count = Object.values(state.answers).filter(Boolean).length;
    document.getElementById('survey-answered-count').textContent = `${count} of ${state.questions.length} questions answered`;
  }

  document.getElementById('survey-prev-btn')?.addEventListener('click', () => {
    if (state.currentStep > 0) renderSurveyStep(state.currentStep - 1);
  });

  document.getElementById('survey-next-btn')?.addEventListener('click', () => {
    if (state.currentStep < state.questions.length - 1) {
      renderSurveyStep(state.currentStep + 1);
    } else {
      triggerRecommendationRanking();
    }
  });

  document.getElementById('btn-get-recommendations')?.addEventListener('click', triggerRecommendationRanking);

  const completedProjectSearch = document.getElementById('completed-project-search');
  const completedProjectToggle = document.getElementById('has-completed-projects');
  const completedProjectControls = document.getElementById('completed-project-controls');
  completedProjectToggle?.addEventListener('change', () => {
    completedProjectControls.hidden = !completedProjectToggle.checked;
    if (!completedProjectToggle.checked) {
      document.getElementById('completed-project-search-results').innerHTML = '';
    }
  });
  let projectSearchTimer;
  completedProjectSearch?.addEventListener('input', () => {
    clearTimeout(projectSearchTimer);
    projectSearchTimer = setTimeout(async () => {
      const query = completedProjectSearch.value.trim();
      const results = document.getElementById('completed-project-search-results');
      if (query.length < 2) {
        results.innerHTML = '';
        return;
      }
      try {
        const response = await api.searchProjects(query);
        const selectedIds = new Set(state.completedProjects.map(project => project.key));
        results.innerHTML = response.projects
          .filter(project => !selectedIds.has(project.key))
          .map(project => `
            <button class="project-search-option" type="button" role="option"
              data-project-key="${escapeHtml(project.key)}"
              data-project-label="${escapeHtml(project.label)}">
              ${escapeHtml(project.label)} <span class="caption">${escapeHtml(project.domain)}</span>
            </button>
          `).join('');
        results.querySelectorAll('[data-project-key]').forEach(button => {
          button.addEventListener('click', () => {
            state.completedProjects.push({
              key: button.dataset.projectKey,
              label: button.dataset.projectLabel
            });
            completedProjectSearch.value = '';
            results.innerHTML = '';
            renderCompletedProjectSelection();
          });
        });
      } catch (err) {
        results.innerHTML = `<div class="caption" style="padding:9px;">${escapeHtml(err.message)}</div>`;
      }
    }, 180);
  });

  function renderCompletedProjectSelection() {
    const container = document.getElementById('completed-project-selection');
    container.innerHTML = state.completedProjects.map(project => `
      <span class="completed-project-chip">
        ${escapeHtml(project.label)}
        <button type="button" data-remove-project="${escapeHtml(project.key)}" aria-label="Remove ${escapeHtml(project.label)}">×</button>
      </span>
    `).join('');
    container.querySelectorAll('[data-remove-project]').forEach(button => {
      button.addEventListener('click', () => {
        state.completedProjects = state.completedProjects.filter(
          project => project.key !== button.dataset.removeProject
        );
        renderCompletedProjectSelection();
      });
    });
  }

  async function triggerRecommendationRanking() {
    try {
      const status = document.getElementById('recommendation-status');
      const surveyStatus = document.getElementById('survey-rank-status');
      surveyStatus.textContent = '';
      const schedule = state.answers.schedule || {};
      const hours = Number(schedule.hours_per_week || state.answers.hours_per_week);
      const deadline = Number(schedule.deadline_weeks || state.answers.deadline_weeks);
      const res = await api.rankProjects(state.answers, 5, {
        hours_per_week: hours || null,
        deadline_weeks: deadline || null,
        completed_project_ids: completedProjectToggle?.checked
          ? state.completedProjects.map(project => project.key)
          : []
      });
      state.recommendationResponse = res;
      if (res.needs_more_info) {
        surveyStatus.textContent = `A little more information is needed: ${(res.missing || []).join(', ')}.`;
        return;
      }
      status.hidden = true;
      state.recommendations = res.results || [];
      renderRecommendations(state.recommendations);
      switchStage('results');
    } catch (err) {
      alert('Ranking failed: ' + err.message);
    }
  }

  // ── 3. Recommendations & Detail Inspector ──
  function renderRecommendations(items) {
    const grid = document.getElementById('recommendations-grid');
    grid.innerHTML = '';
    document.getElementById('recommendation-status').hidden = true;
    items.forEach(item => {
      const card = document.createElement('div');
      card.className = 'card';
      card.style.cursor = 'pointer';
      card.innerHTML = `
        <span class="tier-badge">${escapeHtml(item.tier || 'Baseline')}</span>
        <div style="font-size:1.05rem;font-weight:700;margin:8px 0 4px;">${escapeHtml(item.label)}</div>
        <p class="caption">${escapeHtml(item.desc)}</p>
        <div style="margin:10px 0;font-size:0.8rem;color:var(--text-secondary);">
          ${escapeHtml(item.domain)} · ${item.build_hours || '—'} hrs · ₹${formatPrice(item.cost_inr)}
        </div>
        <button class="btn primary" type="button" data-choose-project style="width:100%;margin-top:6px;">View explanation</button>
        <div class="recommendation-feedback" aria-label="Recommendation feedback">
          <button class="btn ghost" type="button" data-feedback="built">Built</button>
          <button class="btn ghost" type="button" data-feedback="too_hard">Too hard</button>
          <button class="btn ghost" type="button" data-feedback="too_easy">Too easy</button>
          <button class="btn ghost" type="button" data-feedback="not_interested">Not interested</button>
        </div>
        <span class="caption" data-feedback-status role="status"></span>
      `;
      card.addEventListener('click', () => inspectProjectDetail(item));
      card.querySelector('[data-choose-project]').addEventListener('click', event => {
        event.stopPropagation();
        inspectProjectDetail(item);
      });
      card.querySelectorAll('[data-feedback]').forEach(button => {
        button.addEventListener('click', async event => {
          event.stopPropagation();
          const status = card.querySelector('[data-feedback-status]');
          button.disabled = true;
          try {
            const level = Number(state.answers.skill_level || state.answers.level);
            await api.sendRecommendationFeedback(item.key, button.dataset.feedback, {
              level: Number.isInteger(level) && level >= 1 && level <= 5 ? level : null,
              role: String(state.answers.career || 'undecided').slice(0, 40)
            });
            status.textContent = 'Feedback recorded';
          } catch (err) {
            status.textContent = `Could not record feedback: ${err.message}`;
            button.disabled = false;
          }
        });
      });
      grid.appendChild(card);
    });

    renderStarGoal(state.recommendationResponse?.goal);
    if (items.length > 0) inspectProjectDetail(items[0]);
  }

  function renderStarGoal(goal) {
    const container = document.getElementById('star-goal-card');
    if (!goal) {
      container.innerHTML = '<p class="caption">No Star Project path could be calculated. Add your completed projects and survey details to see how many builds remain before a Star Project is recommended.</p>';
      return;
    }
    const steps = goal.step_labels || goal.steps || [];
    const pathMessage = steps.length
      ? `Build ${steps.length} more prerequisite project${steps.length === 1 ? '' : 's'} before ${goal.label} is ready to start as your Star Project goal.`
      : `Your listed prerequisites are complete. ${goal.label} is ready to start as your Star Project goal.`;
    const priceLabel = goal.price_status === 'pending_verification'
      ? 'Price pending verification'
      : 'Rough estimate; not a quote';
    container.innerHTML = `
      <p class="eyebrow">LONG-TERM STAR PROJECT GOAL</p>
      <h3>${escapeHtml(goal.label)}</h3>
      <p class="text-secondary">${escapeHtml(goal.desc)}</p>
      <p class="caption">Estimated cost: ₹${formatPrice(goal.cost_inr)} · ${priceLabel}</p>
      <p class="star-goal-next-step">${escapeHtml(pathMessage)}</p>
      <h4>${steps.length ? 'Projects to complete first' : 'Prerequisite path'}</h4>
      ${steps.length
        ? `<ol class="goal-step-list">${steps.map(step => `<li>${escapeHtml(step)}</li>`).join('')}</ol>`
        : '<p class="caption">No remaining prerequisite projects.</p>'}
    `;
  }

  function inspectProjectDetail(item) {
    state.activeProject = item.key;
    const inspector = document.getElementById('project-detail-inspector');
    inspector.style.display = 'block';

    document.getElementById('detail-project-title').textContent = item.tier
      ? `${item.label} · ${item.tier.replaceAll('_', ' ')}`
      : `${item.label} · baseline recommendation`;
    document.getElementById('detail-project-desc').textContent = item.desc;
    document.getElementById('detail-meta-list').innerHTML = `
      <div><b>Domain:</b> ${escapeHtml(item.domain)}</div>
      <div><b>Estimated build time:</b> ${item.build_hours || '—'} hours</div>
      <div><b>Estimated cost:</b> ₹${formatPrice(item.cost_inr)} <span class="caption">${item.price_status ? `(${escapeHtml(item.price_status.replaceAll('_', ' '))})` : '(rough estimate; not a quote)'}</span></div>
      <div><b>Parts availability in India:</b> ${item.parts_availability_india ?? '—'}/5</div>
      <div><b>Tools required:</b> ${escapeHtml((item.tools_required || []).join(', ') || 'Standard hand tools')}</div>
      <div><b>Required skills:</b> ${escapeHtml((item.required_skills || []).join(', ') || 'See skills to practise')}</div>
    `;

    document.getElementById('detail-reasons').innerHTML = (item.reasons || [])
      .map(reason => `<span class="recommendation-chip">${escapeHtml(reason)}</span>`).join('')
      || '<span class="caption">Reason details are available for the v2 scorer.</span>';
    document.getElementById('detail-learn-skills').innerHTML = (item.learn_skills || [])
      .map(skill => `<span class="recommendation-chip">${escapeHtml(skill)}</span>`).join('')
      || '<span class="caption">No specific new skill tags are listed.</span>';
    document.getElementById('detail-unlocks').innerHTML = (item.unlocks || [])
      .map(key => `<span class="recommendation-chip">${escapeHtml(key.replaceAll('_', ' '))}</span>`).join('')
      || '<span class="caption">No specific next projects are listed.</span>';

    const factorRows = [...(item.fit_breakdown || [])]
      .sort((left, right) => (right.fit_score ?? 0) - (left.fit_score ?? 0));
    const factorTableBody = document.querySelector('#detail-fit-table tbody');
    factorTableBody.replaceChildren();
    factorRows.forEach((row, index) => {
      const tableRow = document.createElement('tr');
      [String(index + 1), row.factor, row.fit].forEach(value => {
        const cell = document.createElement('td');
        cell.textContent = value;
        tableRow.appendChild(cell);
      });
      factorTableBody.appendChild(tableRow);
    });
    document.getElementById('detail-radar-chart').innerHTML = item.chart_svg || '';
    const flagLabels = {
      cost_unverified: 'Cost has not been verified',
      slightly_over_budget: 'Slightly above the stated budget',
      low_information: 'Limited preference information',
      build_first: 'A prerequisite should be built first'
    };
    document.getElementById('detail-flags').textContent = (item.flags || [])
      .map(flag => flagLabels[flag] || flag.replaceAll('_', ' ')).join(' · ');

    // Action button
    const btn = document.getElementById('btn-build-bom-for-pick');
    btn.disabled = !item.bom_supported;
    btn.title = item.bom_supported ? '' : 'BOM generation is not available for this recommendation yet.';
    btn.textContent = item.bom_supported ? '⚡ Build the BOM for this project' : 'BOM not available for this project';
    btn.onclick = async () => {
      if (!item.bom_supported) return;
      try {
        await openGuidedBom({ project: item.key, budget: item.cost_inr || 3500 });
      } catch (err) {
        alert('Could not open BOM: ' + err.message);
      }
    };
  }

  // Free-text Idea Match
  document.getElementById('btn-submit-idea')?.addEventListener('click', async () => {
    const input = document.getElementById('input-idea-match');
    const txt = (input.value || '').trim();
    if (!txt) return;
    try {
      const res = await api.matchIdea(txt);
      const out = document.getElementById('idea-match-result');
      if (res.supported && res.project) {
        out.innerHTML = `<div class="alert success">Closest supported project: <b>${res.label}</b>. <button class="btn primary" id="btn-use-matched-idea" style="margin-left:10px;">Build BOM</button></div>`;
        document.getElementById('btn-use-matched-idea')?.addEventListener('click', async () => {
          await openGuidedBom({ project: res.project });
        });
      } else {
        out.innerHTML = `<div class="alert warn">${res.reason}</div>`;
      }
    } catch (err) {
      alert('Idea match failed: ' + err.message);
    }
  });

  // ── 4. Competition Shortlist ──
  async function loadCompetitionEvents() {
    try {
      const evs = await api.getCompetitionEvents();
      const sel = document.getElementById('select-competition-event');
      sel.innerHTML = '';
      evs.forEach(e => {
        const opt = document.createElement('option');
        opt.value = e.name;
        opt.textContent = `${e.name} · ${e.mentions} project mentions`;
        sel.appendChild(opt);
      });
      sel.onchange = () => loadEventProjects(sel.value);
      if (evs.length > 0) loadEventProjects(evs[0].name);
    } catch (err) {
      console.error('Failed to load competition events:', err);
    }
  }

  async function loadEventProjects(eventName) {
    try {
      const res = await api.getCompetitionProjects(eventName);
      const container = document.getElementById('competition-projects-container');
      container.innerHTML = '';
      (res.projects || []).forEach(p => {
        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <h3>${p.label}</h3>
            <button class="btn primary btn-comp-plan" data-key="${p.key}">⚡ Plan &amp; Build BOM</button>
          </div>
          <p class="caption">${p.desc}</p>
          <div style="margin:10px 0;font-size:0.82rem;color:var(--text-secondary);">
            Estimated ${p.build_hours} hours · ₹${formatPrice(p.cost_inr)} · Availability ${p.parts_availability_india}/5
          </div>
          <div class="caption"><b>Constraints:</b> ${p.constraints || 'Standard competition constraints'}</div>
        `;
        card.querySelector('.btn-comp-plan').onclick = async () => {
          try {
            await openGuidedBom({ project: p.key, budget: p.cost_inr || 3500 });
          } catch (err) {
            alert('Failed to generate competition BOM: ' + err.message);
          }
        };
        container.appendChild(card);
      });
    } catch (err) {
      console.error('Failed to load event projects:', err);
    }
  }

  // ── 5. Planning & BOM Studio ──
  function populateCatalogDropdown(options) {
    const sel = document.getElementById('select-catalog-project');
    if (!sel) return;
    sel.innerHTML = '';
    options.forEach(o => {
      const opt = document.createElement('option');
      opt.value = o.key;
      opt.textContent = o.label;
      sel.appendChild(opt);
    });
  }

  document.getElementById('btn-generate-catalog-bom')?.addEventListener('click', async () => {
    const sel = document.getElementById('select-catalog-project');
    const budget = parseInt(document.getElementById('bom-budget-input').value) || 3500;
    const bt = document.getElementById('bom-bluetooth-toggle').checked;
    try {
      const bom = await api.generateBom({ project: sel.value, budget, bluetooth: bt });
      renderBomView(bom);
    } catch (err) {
      alert('Error generating BOM: ' + err.message);
    }
  });

  document.getElementById('btn-plan-custom-build')?.addEventListener('click', async () => {
    const inp = document.getElementById('input-custom-build');
    const txt = (inp.value || '').trim();
    if (!txt) return;
    const budget = parseInt(document.getElementById('bom-budget-input').value) || 3500;
    const bt = document.getElementById('bom-bluetooth-toggle').checked;
    try {
      const bom = await api.generateBom({ custom_idea: txt, budget, bluetooth: bt });
      if (bom.status === 'suggest') {
        alert('Suggested similar catalog projects available.');
      } else {
        renderBomView(bom);
      }
    } catch (err) {
      alert('Planning custom build failed: ' + err.message);
    }
  });

  async function loadInitialBom() {
    try {
      const bom = await api.generateBom({ project: 'general:line_follower', budget: 3500 });
      renderBomView(bom);
    } catch (err) {
      console.warn('Initial BOM load:', err);
    }
  }

  function renderBomView(bom) {
    if (!bom) return;
    state.activeBom = bom;
    state.wiringData = null;
    state.optimizerEvaluation = null;
    updateWorkflowNavigation();

    const heading = document.getElementById('bom-project-heading');
    if (heading) heading.textContent = `${bom.label || 'Project'} — Architecture & Planning`;

    const billMetaProject = document.getElementById('bill-meta-project');
    if (billMetaProject) billMetaProject.textContent = bom.label || '';

    const totCost = bom.bill?.grand_total ?? bom.bill?.parts_subtotal ?? 0;
    const costElem = document.getElementById('metric-total-cost');
    if (costElem) costElem.textContent = `₹${formatPrice(totCost)}`;

    const partsCountElem = document.getElementById('metric-components-count');
    if (partsCountElem) partsCountElem.textContent = `${bom.parts_count || 0} parts`;

    const liveCountElem = document.getElementById('metric-live-priced');
    if (liveCountElem) liveCountElem.textContent = `${bom.bill?.live_priced_count || 0} / ${bom.parts_count || 0} live`;

    // Render Origin Banner
    const originMap = {
      rule: 'Rule-checked build: parts chosen by compatibility rules (no AI).',
      dataset: 'Component list read from the FORGE dataset. No Gemini call.',
      learned: 'Reused a plan Gemini made earlier for this idea. No new Gemini call.',
      gemini: 'Gemini planned this build.'
    };
    const banner = document.getElementById('bom-origin-banner');
    if (banner) {
      banner.innerHTML = `<div class="alert success">${originMap[bom.origin] || 'Verified architecture.'}</div>`;
    }

    renderComponentCards(bom.parts || {}, bom.bill?.lines || [], bom.parts_offers || {}, bom.choice || {}, bom.manual || {});
    renderOptimizerRoles();

    const optContent = document.getElementById('optimizer-content');
    if (optContent) optContent.hidden = !state.optimizerOpen;

    const optToggleBtn = document.getElementById('btn-toggle-optimizer');
    if (optToggleBtn) {
      optToggleBtn.setAttribute('aria-expanded', String(state.optimizerOpen));
      optToggleBtn.textContent = state.optimizerOpen ? 'Close optimizer' : 'Open optimizer';
    }

    const optRes = document.getElementById('optimizer-result');
    if (optRes) optRes.hidden = true;

    const optActions = document.getElementById('optimizer-actions');
    if (optActions) optActions.hidden = true;

    if (state.optimizerOpen) loadOptimizerCandidates();
    renderChecks(bom.checks || []);
    renderBillTable(bom.bill);
    renderBuildGuide();
    loadWiringStudio();
    loadDeliveryOptimizer();
    loadResearchGuides();
    loadSpecsStudio();
    loadEvidenceLedger();
  }

  function renderComponentCards(parts, lines, partsOffers = {}, choice = {}, manual = {}) {
    const container = document.getElementById('bom-components-container');
    container.innerHTML = '';
    const lineMap = {};
    lines.forEach(l => { lineMap[l.part_id] = l; });

    Object.entries(parts).forEach(([role, part]) => {
      if (part.none) return;
      const line = lineMap[part.id] || { qty: 1, unit: part.seed_price ?? null, source: 'estimate' };
      const statusLabel = line.source === 'live' ? 'LIVE PRICE' : (line.source === 'manual' ? 'YOUR PICK' : 'ESTIMATED');
      const badgeCls = line.source === 'live' ? 'PASS' : (line.source === 'manual' ? 'WARN' : 'UNKNOWN');
      const priceText = (line.unit !== null && line.unit !== undefined) ? `₹${formatPrice(line.unit * line.qty)}` : '—';
      const offerInfo = partsOffers[part.id] || { verified: [], unverified: [] };
      const chosenUrl = manual[part.id] ? null : (choice[part.id] || (offerInfo.verified[0]?.url));

      const card = document.createElement('div');
      card.className = 'component-card';

      let offersHtml = '';
      if (offerInfo.warning) {
        offersHtml += `<div class="alert warning" style="margin-bottom:8px;">${offerInfo.warning}</div>`;
      }

      if (offerInfo.verified && offerInfo.verified.length > 0) {
        offersHtml += `<div style="font-size:0.75rem;font-weight:700;color:var(--text-secondary);margin:8px 0 6px 0;letter-spacing:0.04em;">VERIFIED PRICES · LOWEST FIRST</div>`;
        offerInfo.verified.forEach(o => {
          const isSelected = (chosenUrl === o.url);
          offersHtml += `
            <div class="offer-row ${isSelected ? 'selected' : ''}">
              <div style="flex:1;">
                <div style="display:flex;align-items:center;gap:8px;">
                  <b>${o.vendor}</b>
                  <span class="caption">${(o.name || '').slice(0, 70)}</span>
                  ${o.suspect ? '<span class="b FAIL">CHECK SUSPECT</span>' : ''}
                </div>
                <div class="caption" style="margin-top:4px;">
                  ${o.quote ? `Evidence: “${o.quote.slice(0, 100)}” · ` : ''}
                  Checked ${o.checked_at || 'live'} · Method: ${o.method || 'page rules'}
                </div>
              </div>
              <div style="text-align:right;min-width:140px;">
                <div style="font-size:1.1rem;font-weight:700;color:var(--primary-orange);">₹${formatPrice(o.price)}</div>
                <div style="display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-top:6px;">
                  <a href="${o.url}" target="_blank" rel="noopener noreferrer" class="caption" style="color:var(--text-secondary);text-decoration:underline;">View product ↗</a>
                  ${isSelected
                    ? '<span class="b PASS">SELECTED</span>'
                    : `<button class="btn btn-sm btn-pick-offer" data-pid="${part.id}" data-url="${o.url}">Use this</button>`}
                </div>
              </div>
            </div>
          `;
        });
      }

      if (offerInfo.unverified && offerInfo.unverified.length > 0) {
        offersHtml += `
          <div style="font-size:0.75rem;font-weight:700;color:var(--text-secondary);margin:14px 0 6px 0;letter-spacing:0.04em;">PRICE NOT VERIFIED</div>
          <p class="caption">These pages were found, but no price could be confirmed from their text. You may choose one and type the price you saw:</p>
        `;
        offerInfo.unverified.forEach(uo => {
          const manualEntry = manual[part.id] || {};
          const isManual = (manualEntry.url === uo.url);
          offersHtml += `
            <div class="offer-row ${isManual ? 'selected' : ''}" style="margin-top:6px;">
              <div style="flex:1;">
                <div style="display:flex;align-items:center;gap:8px;">
                  <b>${uo.vendor}</b>
                  <span class="caption">${(uo.name || 'Listing name not verified').slice(0, 60)}</span>
                  <span class="b UNKNOWN">PRICE NOT VERIFIED</span>
                  ${isManual ? '<span class="b WARN">YOUR PICK</span>' : ''}
                </div>
                <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
                  <input type="number" class="manual-price-val" placeholder="Price seen (₹)" style="width:130px;padding:4px 8px;font-size:0.85rem;" value="${isManual ? (manualEntry.price || '') : ''}">
                  <button class="btn btn-sm btn-save-manual" data-pid="${part.id}" data-url="${uo.url}" data-name="${uo.name || ''}">Use this listing</button>
                  <a href="${uo.url}" target="_blank" rel="noopener noreferrer" class="caption" style="color:var(--text-secondary);text-decoration:underline;margin-left:8px;">View product ↗</a>
                </div>
              </div>
            </div>
          `;
        });
      }

      if (!offerInfo.verified?.length && !offerInfo.unverified?.length) {
        if (part.seed_price !== null && part.seed_price !== undefined) {
          offersHtml += `<p class="caption">No verified shop price found yet. Using team estimate of ₹${formatPrice(part.seed_price)} (not checked on any page).</p>`;
        } else {
          offersHtml += `<p class="caption">No verified shop price found yet. This part is not included in the total until a price is verified or entered.</p>`;
        }
        offersHtml += `<p class="caption" style="margin-top:6px;color:var(--primary-orange);">Click <b>⚡ Find live prices</b> above to scan vendors with TinyFish.</p>`;
      }

      card.innerHTML = `
        <div class="component-card-header">
          <div style="display:flex;align-items:center;gap:12px;">
            <span class="arrow-icon">▶</span>
            <b>${part.name}</b>
            <span class="caption" style="text-transform:uppercase;">${role} × ${line.qty}</span>
            <span class="b ${badgeCls}">${statusLabel}</span>
          </div>
          <div style="font-weight:700;">${priceText}</div>
        </div>
        <div class="component-card-body">
          <p class="caption">${part.note || part.why || 'Engineering component'}</p>
          <div class="offer-list" style="margin-top:10px;">
            ${offersHtml}
          </div>
        </div>
      `;

      card.querySelector('.component-card-header').onclick = () => {
        card.classList.toggle('open');
      };

      // Button handlers inside card
      card.querySelectorAll('.btn-pick-offer').forEach(btn => {
        btn.onclick = async (e) => {
          e.stopPropagation();
          const pid = btn.dataset.pid;
          const url = btn.dataset.url;
          await api.setChoice('pick', pid, url);
          const updatedBom = await api.getBom();
          renderBomView(updatedBom);
        };
      });

      card.querySelectorAll('.btn-save-manual').forEach(btn => {
        btn.onclick = async (e) => {
          e.stopPropagation();
          const pid = btn.dataset.pid;
          const url = btn.dataset.url;
          const name = btn.dataset.name;
          const parentRow = btn.closest('.offer-row');
          const input = parentRow.querySelector('.manual-price-val');
          const val = parseFloat(input?.value);
          if (isNaN(val) || val <= 0) {
            alert('Please enter a valid price in ₹');
            return;
          }
          await api.setChoice('manual', pid, url, val, name);
          const updatedBom = await api.getBom();
          renderBomView(updatedBom);
        };
      });

      container.appendChild(card);
    });
  }

  function renderChecks(checks) {
    const list = document.getElementById('bom-checks-list');
    list.innerHTML = '';
    if (checks.length === 0) {
      list.innerHTML = '<div class="caption">Architecture follows standard rule constraints.</div>';
      return;
    }
    checks.forEach(c => {
      const row = document.createElement('div');
      row.style.margin = '4px 0';
      row.innerHTML = `<span class="b ${c.status}">${c.status}</span> <span style="font-weight:600;margin-left:6px;">${c.title}</span> <span class="caption">${c.msg || ''}</span>`;
      list.appendChild(row);
    });
  }

  function renderBillTable(billData) {
    if (!billData) return;
    const container = document.getElementById('bill-invoice-container');
    if (!container) return;

    if (billData.html) {
      container.innerHTML = billData.html;
      return;
    }

    const e = escapeHtml;
    const lines = billData.lines || [];
    const tot = billData.tot || {};
    const ref = billData.ref || 'FG-001';
    const day = billData.date || new Date().toISOString().slice(0, 10);
    const subtotal = billData.grand_total ?? billData.total ?? billData.parts_subtotal ?? tot.subtotal ?? 0;
    const budget = tot.budget ?? state.activeBom?.budget ?? 0;
    const left = budget - subtotal;
    const isOver = left < -0.005;

    const rowsHtml = lines.map(l => {
      let kind = 'est';
      let statusText = 'Estimated catalog price';
      if (l.source === 'live') {
        kind = 'best';
        statusText = 'Cheapest online price';
      } else if (l.source === 'manual') {
        const prem = Math.max(0, (l.total || 0) - (l.cheapest_total || l.total || 0));
        kind = 'pick';
        statusText = `Your pick, +₹${formatPrice(prem)}`;
      }
      const shopLink = l.url
        ? `<a href="${e(l.url)}" target="_blank" rel="noopener noreferrer">${e(l.vendor || 'Shop')}</a>`
        : `<span class="rl">No shop page verified</span>`;
      const listingText = l.listing ? `<div class="rl">${e(l.listing.slice(0, 70))}</div>` : '';
      const unitText = (l.unit !== null && l.unit !== undefined) ? `₹${formatPrice(l.unit)}` : '—';
      const totText = (l.total !== null && l.total !== undefined) ? `₹${formatPrice(l.total)}` : '—';

      return `
        <tr>
          <td>
            <div class="nm">${e(l.name)}</div>
            <div class="rl">${e((l.role || '').toUpperCase())}</div>
          </td>
          <td>
            ${shopLink}
            ${listingText}
            <div><span class="fg-c ${kind}">${e(statusText)}</span></div>
          </td>
          <td class="n">${l.qty}</td>
          <td class="n">${unitText}</td>
          <td class="n amt">${totText}</td>
        </tr>
      `;
    }).join('');

    const nLive = tot.n_live ?? lines.filter(l => l.source === 'live').length;
    const pricesText = nLive === lines.length
      ? `All ${lines.length} live from shops`
      : (nLive ? `${nLive} of ${lines.length} live from shops` : 'Estimated catalog seed prices');

    let extraHtml = '';
    if (tot.premium && tot.premium > 0.005) {
      extraHtml += `
        <div class="fg-row"><span>Cheapest possible total</span><b>₹${formatPrice(tot.cheapest)}</b></div>
        <div class="fg-row"><span>Extra for your picks</span><b>+₹${formatPrice(tot.premium)}</b></div>
      `;
    }
    if (tot.n_missing) {
      extraHtml += `<div class="fg-row"><span>Parts without a verified price</span><b>${tot.n_missing} not in total</b></div>`;
    }

    container.innerHTML = `
      <div class="fg-sheet">
        <div class="fg-head">
          <div>
            <div class="fg-brand">FORGE</div>
            <div class="fg-sub">Estimated bill of materials</div>
          </div>
          <div class="fg-ref">
            Reference <b>${e(ref)}</b><br>
            ${tot.n_items || lines.reduce((a, b) => a + (b.qty || 1), 0)} items across ${lines.length} parts
          </div>
        </div>
        <div class="fg-meta">
          <div><span>Project</span><b>${e(state.activeBom?.label || state.activeProject || 'Robot Build')}</b></div>
          <div><span>Prepared on</span><b>${e(day)}</b></div>
          <div><span>Budget</span><b>₹${formatPrice(budget)}</b></div>
          <div><span>Prices</span><b>${e(pricesText)}</b></div>
        </div>
        <div class="fg-wrap">
          <table class="fg-t">
            <thead>
              <tr>
                <th>Part</th>
                <th>Shop</th>
                <th class="n">Qty</th>
                <th class="n">Unit price</th>
                <th class="n">Amount</th>
              </tr>
            </thead>
            <tbody>
              ${rowsHtml}
            </tbody>
          </table>
        </div>
        <div class="fg-tot">
          <div>
            ${extraHtml}
            <div class="fg-row ${isOver ? 'bad' : 'ok'}">
              <span>${isOver ? 'Over budget by' : 'Under budget by'}</span>
              <b>₹${formatPrice(Math.abs(left))}</b>
            </div>
            <div class="fg-grand">
              <span>Estimated total</span>
              <b>₹${formatPrice(subtotal)}</b>
            </div>
          </div>
        </div>
        <div class="fg-note">Estimate only; verify price, delivery and stock with each vendor before purchasing.</div>
      </div>
    `;
  }

  // ── 6. Live Sourcing SSE Stream ──
  document.getElementById('btn-find-live-prices')?.addEventListener('click', () => {
    const box = document.getElementById('sourcing-log-box');
    box.style.display = 'block';
    box.textContent = 'Connecting to TinyFish live sourcing stream...\n';

    api.streamSourcing(
      (line) => {
        box.textContent += line + '\n';
        box.scrollTop = box.scrollHeight;
      },
      async () => {
        box.textContent += 'Done! Re-computing verified bill...\n';
        const updatedBom = await api.getBom();
        renderBomView(updatedBom);
      }
    );
  });

  // ── 7. Hardware Wiring Studio ──
  let mermaidRenderCount = 0;

  async function renderMermaidDiagram(source) {
    const container = document.getElementById('mermaid-diagram');
    const status = document.getElementById('mermaid-render-status');
    if (!container || !status) return;

    container.replaceChildren();
    if (!source) {
      status.textContent = 'No Mermaid wiring data is available for this build.';
      return;
    }
    if (!window.mermaid) {
      status.textContent = 'Diagram renderer unavailable; use the Mermaid source below.';
      return;
    }

    try {
      window.mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'dark',
        flowchart: { htmlLabels: false, curve: 'basis' }
      });
      const rendered = await window.mermaid.render(
        `forge-wiring-diagram-${++mermaidRenderCount}`,
        source
      );
      container.innerHTML = rendered.svg;
      status.textContent = 'Diagram generated from the active build wiring.';
    } catch (err) {
      status.textContent = 'Diagram rendering failed; use the Mermaid source below.';
      console.warn('Could not render Mermaid wiring diagram:', err);
    }
  }

  async function loadWiringStudio() {
    try {
      const w = await api.getWiring();
      state.wiringData = w;
      document.getElementById('wiring-svg-container').innerHTML = w.svg_canvas || '';
      document.getElementById('wokwi-json-preview').value = w.wokwi_json_str || '{}';
      document.getElementById('firmware-code-preview').textContent = w.arduino_code || '';
      state.componentTests = w.component_tests || [];
      renderComponentTestBench();
      document.getElementById('mermaid-source-preview').textContent = w.mermaid_code || '';
      await renderMermaidDiagram(w.mermaid_code || '');

      const pbody = document.querySelector('#pinout-table tbody');
      pbody.innerHTML = '';
      (w.connections || []).forEach(c => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${c.from_part || ''} ${c.from_pin}</td><td>➔</td><td>${c.to_pin}</td><td>${c.color || 'Signal'}</td>`;
        pbody.appendChild(tr);
      });
      renderBuildGuide();
    } catch (err) {
      console.warn('Could not load wiring studio:', err);
      state.wiringData = null;
      renderBuildGuide();
    }
  }

  function renderComponentTestBench() {
    const selector = document.getElementById('component-test-select');
    const wiringList = document.getElementById('component-test-wiring');
    const code = document.getElementById('component-test-code');
    if (!selector || !wiringList || !code) return;

    const guides = state.componentTests || [];
    const previous = selector.value;
    selector.replaceChildren();
    guides.forEach((guide, index) => {
      const option = document.createElement('option');
      option.value = String(index);
      option.textContent = `${guide.name}${guide.supported ? '' : ' · no test sketch yet'}`;
      selector.appendChild(option);
    });
    if (!guides.length) {
      selector.innerHTML = '<option value="">No testable BOM components</option>';
      document.getElementById('component-test-status').textContent = 'Generate a BOM with supported component wiring to create an isolated test guide.';
      wiringList.replaceChildren();
      code.textContent = '';
      document.getElementById('component-test-expected').textContent = '';
      document.getElementById('component-test-steps').textContent = '';
      return;
    }

    selector.value = Number.isInteger(Number(previous)) && guides[Number(previous)] ? previous : '0';
    const guide = guides[Number(selector.value)];
    const status = document.getElementById('component-test-status');
    status.textContent = guide.supported
      ? `${guide.role} · ${guide.library ? 'Additional library required' : 'Built-in Arduino APIs only'}`
      : 'This component is shown in the BOM, but its pins are not modeled well enough for FORGE to generate a standalone test.';

    wiringList.replaceChildren();
    guide.wiring.forEach(step => {
      const item = document.createElement('li');
      item.textContent = step;
      wiringList.appendChild(item);
    });
    if (!guide.wiring.length) {
      const item = document.createElement('li');
      item.textContent = 'No generated pin mapping is available for this part.';
      wiringList.appendChild(item);
    }
    code.textContent = guide.code || '// No component-specific sketch available.';
    document.getElementById('component-test-expected').textContent = guide.expected;
    document.getElementById('component-test-steps').textContent = `${guide.steps}${guide.library ? ` ${guide.library}` : ''}`;
    const safety = document.getElementById('component-test-safety');
    safety.textContent = guide.safety || '';
    safety.hidden = !guide.safety;
    document.getElementById('btn-copy-component-test').disabled = !guide.supported;
  }

  document.getElementById('component-test-select')?.addEventListener('change', renderComponentTestBench);
  document.getElementById('btn-copy-component-test')?.addEventListener('click', async () => {
    const source = document.getElementById('component-test-code').textContent;
    const status = document.getElementById('component-test-status');
    try {
      await navigator.clipboard.writeText(source);
      status.textContent = 'Test sketch copied. Paste it into a new Arduino IDE sketch.';
    } catch (err) {
      status.textContent = 'Could not access clipboard. Select the sketch text and copy it manually.';
    }
  });

  // Wiring Tabs
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const tabKey = btn.dataset.wiringTab;
      document.querySelectorAll('.wiring-tab-pane').forEach(p => p.style.display = 'none');
      const target = document.getElementById(`tab-content-${tabKey}`);
      if (target) target.style.display = 'block';
    });
  });

  document.getElementById('btn-copy-mermaid')?.addEventListener('click', async () => {
    const source = document.getElementById('mermaid-source-preview').textContent;
    const status = document.getElementById('mermaid-render-status');
    try {
      await navigator.clipboard.writeText(source);
      status.textContent = 'Mermaid source copied.';
    } catch (err) {
      status.textContent = 'Could not copy source; select it from “View Mermaid source”.';
      console.warn('Could not copy Mermaid source:', err);
    }
  });

  document.getElementById('btn-prepare-wokwi')?.addEventListener('click', () => {
    window.open('https://wokwi.com/', '_blank', 'noopener,noreferrer');
    document.getElementById('wokwi-open-status').textContent =
      'The starter bundle is downloading and Wokwi is opening. Import diagram.json and sketch.ino there, then start the simulation.';
  });

  // ── 8. Delivery Optimizer & Packages Lineup ──
  async function loadDeliveryOptimizer() {
    try {
      const opt = await api.optimizeDelivery();
      const grid = document.getElementById('delivery-strategies-grid');
      if (!grid) return;
      grid.innerHTML = '';

      const split = opt.split || {};
      const cons = opt.consolidated;
      const smart = opt.smart || {};
      const current = opt.current || {};
      const currentPkg = current.packages_info || smart.packages_info || split.packages_info || { packages: [] };
      const currentChoice = current.choice || {};

      // Determine active strategy
      const isSplitActive = Object.keys(currentChoice).length > 0 &&
        JSON.stringify(currentChoice) === JSON.stringify(split.choice);
      const isConsActive = Boolean(cons && Object.keys(currentChoice).length > 0 &&
        JSON.stringify(currentChoice) === JSON.stringify(cons.choice));
      const isSmartActive = Object.keys(currentChoice).length > 0 &&
        JSON.stringify(currentChoice) === JSON.stringify(smart.choice);

      // Strategy Card 1: SPLIT STORES
      const splitCard = document.createElement('div');
      splitCard.className = `delivery-card ${isSplitActive ? 'active-strategy' : ''}`;
      splitCard.innerHTML = `
        <div>
          <div class="delivery-tag" style="color: #94A3B8;">
            <span>🏪 Split Store</span>
          </div>
          <div class="delivery-subheading">Cheapest Item Prices</div>
          <div class="delivery-price">₹${formatPrice(split.packages_info?.total_landing_cost ?? 0)}</div>
          <div class="delivery-cost-breakdown">
            <div class="delivery-cost-row">
              <span>Parts Subtotal:</span>
              <b>₹${formatPrice(split.packages_info?.total_parts ?? 0)}</b>
            </div>
            <div class="delivery-cost-row">
              <span>Est. Delivery:</span>
              <b>₹${formatPrice(split.packages_info?.total_shipping ?? 0)} (${split.packages_info?.n_packages ?? 0} stores)</b>
            </div>
          </div>
          <div class="delivery-badge-pill">
            <span>📦 ${split.packages_info?.n_packages ?? 0} separate packages</span>
          </div>
        </div>
        <button type="button" class="btn" style="width: 100%; margin-top: 8px;">
          ${isSplitActive ? '✓ Currently Active' : 'Apply Strategy'}
        </button>
      `;
      splitCard.querySelector('button').onclick = async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.textContent = 'Applying...';
        try {
          await api.applyDeliveryStrategy('split');
          const updated = await api.getBom();
          renderBomView(updated);
        } catch (err) {
          console.error('Failed to apply split sourcing:', err);
          btn.disabled = false;
          btn.textContent = 'Apply Strategy';
        }
      };
      grid.appendChild(splitCard);

      // Strategy Card 2: CONSOLIDATED STORE
      const consCard = document.createElement('div');
      consCard.className = `delivery-card ${isConsActive ? 'active-strategy' : ''}`;
      if (cons) {
        const isFreeShip = cons.shipping_cost === 0;
        const shipLabel = isFreeShip ? '<span class="free-ship-tag">Free Delivery 🎉</span>' : `₹${formatPrice(cons.shipping_cost)}`;
        consCard.innerHTML = `
          <div>
            <div class="delivery-tag" style="color: #38BDF8;">
              <span>🏢 Consolidated (${escapeHtml(cons.vendor)})</span>
            </div>
            <div class="delivery-subheading">Coverage: ${cons.coverage}/${cons.total_parts} parts (${Math.round(cons.coverage_pct)}% in 1 box)</div>
            <div class="delivery-price">₹${formatPrice(cons.landing_cost ?? 0)}</div>
            <div class="delivery-cost-breakdown">
              <div class="delivery-cost-row">
                <span>Parts Subtotal:</span>
                <b>₹${formatPrice(cons.parts_cost ?? 0)}</b>
              </div>
              <div class="delivery-cost-row">
                <span>Est. Delivery:</span>
                <b>${shipLabel}</b>
              </div>
            </div>
            <div class="delivery-badge-pill">
              <span>📦 ${cons.n_packages ?? 1} package(s)</span>
            </div>
          </div>
          <button type="button" class="btn" style="width: 100%; margin-top: 8px;">
            ${isConsActive ? '✓ Currently Active' : `Consolidate at ${escapeHtml(cons.vendor)}`}
          </button>
        `;
        consCard.querySelector('button').onclick = async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          btn.textContent = 'Applying...';
          try {
            await api.applyDeliveryStrategy('consolidated');
            const updated = await api.getBom();
            renderBomView(updated);
          } catch (err) {
            console.error('Failed to apply consolidated sourcing:', err);
            btn.disabled = false;
            btn.textContent = 'Apply Strategy';
          }
        };
      } else {
        consCard.innerHTML = `
          <div>
            <div class="delivery-tag" style="color: #38BDF8;">
              <span>🏢 Consolidated Store</span>
            </div>
            <div class="delivery-subheading">Single Store Coverage</div>
            <p class="caption text-muted" style="margin: 16px 0;">No single dominant vendor identified across verified offers.</p>
          </div>
          <button type="button" class="btn" disabled style="width: 100%; margin-top: 8px;">Unavailable</button>
        `;
      }
      grid.appendChild(consCard);

      // Strategy Card 3: SMART LANDING COST
      const smartCard = document.createElement('div');
      smartCard.className = `delivery-card smart ${isSmartActive ? 'active-strategy' : ''}`;
      const smartFreeShip = smart.packages_info?.total_shipping === 0;
      const smartShipLabel = smartFreeShip ? '<span class="free-ship-tag">Free Delivery 🎉</span>' : `₹${formatPrice(smart.packages_info?.total_shipping ?? 0)}`;
      const hasSavings = (smart.savings_vs_split || 0) > 0;
      smartCard.innerHTML = `
        <div>
          <div class="delivery-tag" style="color: #10B981;">
            <span>⚡ Smart Landing Cost</span>
          </div>
          <div class="delivery-subheading" style="color: #A7F3D0;">${escapeHtml(smart.name || 'Optimized Sourcing')}</div>
          <div class="delivery-price" style="color: var(--primary-orange-light);">₹${formatPrice(smart.packages_info?.total_landing_cost ?? 0)}</div>
          <div class="delivery-cost-breakdown">
            <div class="delivery-cost-row">
              <span>Parts Subtotal:</span>
              <b>₹${formatPrice(smart.packages_info?.total_parts ?? 0)}</b>
            </div>
            <div class="delivery-cost-row">
              <span>Est. Delivery:</span>
              <b>${smartShipLabel}</b>
            </div>
          </div>
          <div class="delivery-badge-pill ${hasSavings ? 'savings' : ''}">
            <span>${hasSavings ? `💰 Saves ₹${formatPrice(smart.savings_vs_split)} in delivery fees!` : 'Lowest overall out-of-pocket landing cost'}</span>
          </div>
        </div>
        <button type="button" class="btn primary" style="width: 100%; margin-top: 8px; font-weight: 700;">
          ${isSmartActive ? '✓ Currently Active' : '👉 Apply Strategy'}
        </button>
      `;
      smartCard.querySelector('button').onclick = async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.textContent = 'Applying...';
        try {
          await api.applyDeliveryStrategy('smart');
          const updated = await api.getBom();
          renderBomView(updated);
        } catch (err) {
          console.error('Failed to apply smart sourcing:', err);
          btn.disabled = false;
          btn.textContent = 'Apply Strategy';
        }
      };
      grid.appendChild(smartCard);

      // 1-Click Multi-Store Cart Actions (Open Tabs)
      const allLines = current.lines || [];
      const liveUrls = allLines.map(l => l.url).filter(Boolean);
      const openAllBtn = document.getElementById('btn-open-all-links');
      if (openAllBtn) {
        openAllBtn.textContent = `🚀 Open All Store Tabs (${liveUrls.length})`;
        openAllBtn.onclick = () => {
          if (!liveUrls.length) {
            alert('No verified store URLs found in this configuration.');
            return;
          }
          let opened = 0;
          liveUrls.forEach(u => {
            const w = window.open(u, '_blank');
            if (w) opened++;
          });
          if (opened === 0) {
            alert('Please allow popups in your browser to open all store tabs at once!');
          }
        };
      }

      // Grouped Vendor Packages Lineup (Breakdown)
      const pkgSection = document.getElementById('delivery-packages-section');
      if (pkgSection) {
        pkgSection.innerHTML = `
          <details class="packages-lineup-details" open>
            <summary class="packages-lineup-summary">
              <span>📦 Vendor Packages Lineup (${currentPkg.n_packages || 0} Shipments · ₹${formatPrice(currentPkg.total_landing_cost || 0)} Landing Cost)</span>
              <span class="caption text-muted" style="font-weight: 500;">Click to collapse/expand</span>
            </summary>
            <div class="package-cards-grid" id="packages-cards-list"></div>
          </details>
        `;

        const cardsList = document.getElementById('packages-cards-list');
        (currentPkg.packages || []).forEach((pkg, idx) => {
          const isPkgFreeShip = pkg.free_shipping;
          const pkgShipText = isPkgFreeShip ? '<span class="free-ship-tag">Free Delivery 🎉</span>' : `₹${formatPrice(pkg.shipping)}`;
          const box = document.createElement('div');
          box.className = 'package-box';
          box.innerHTML = `
            <div class="package-box-header">
              <div class="package-vendor-title">
                <span>📦 Package ${idx + 1}:</span>
                <span class="package-vendor-badge">${escapeHtml(pkg.vendor)}</span>
                <span class="caption text-muted">(${pkg.item_count} item${pkg.item_count > 1 ? 's' : ''})</span>
              </div>
              <div class="package-meta">
                <span>Subtotal: <b>₹${formatPrice(pkg.subtotal)}</b></span>
                <span>Delivery: <b>${pkgShipText}</b></span>
              </div>
            </div>
            <div class="package-items-table"></div>
          `;

          const itemsContainer = box.querySelector('.package-items-table');
          (pkg.items || []).forEach(it => {
            const itemRow = document.createElement('div');
            itemRow.className = 'package-item-row';
            itemRow.innerHTML = `
              <div class="package-item-left">
                <span class="package-item-dot">•</span>
                <span class="package-item-name">${escapeHtml(it.name)}</span>
                <span class="package-item-qty">× ${it.qty}</span>
              </div>
              <div class="package-item-right">
                <span class="package-item-price">₹${formatPrice(it.total)}</span>
                ${it.url ? `<a href="${escapeHtml(it.url)}" target="_blank" rel="noopener noreferrer" class="package-item-link">Shop Listing ↗</a>` : '<span class="caption text-muted">*(Seed estimate)*</span>'}
              </div>
            `;
            itemsContainer.appendChild(itemRow);
          });
          cardsList.appendChild(box);
        });
      }

      // Verified Store URLs List
      const linksSection = document.getElementById('delivery-links-section');
      if (linksSection) {
        if (liveUrls.length > 0) {
          linksSection.innerHTML = `
            <details class="verified-urls-details">
              <summary class="caption text-muted" style="cursor: pointer; font-weight: 600;">
                🔗 All Verified Store URLs (${liveUrls.length} direct purchase links)
              </summary>
              <div class="verified-urls-list">
                ${allLines.filter(l => l.url).map((l, i) => `
                  <div class="verified-url-item">
                    <span><b>${i + 1}. ${escapeHtml(l.name)}</b> <span class="caption text-muted">(${escapeHtml(l.vendor || '')})</span></span>
                    <a href="${escapeHtml(l.url)}" target="_blank" rel="noopener noreferrer" class="package-item-link">${escapeHtml(l.url)} ↗</a>
                  </div>
                `).join('')}
              </div>
            </details>
          `;
        } else {
          linksSection.innerHTML = '';
        }
      }
    } catch (err) {
      console.warn('Delivery optimizer:', err);
    }
  }

  // ── 9. Component Datasheet Specs & Comparison Studio ──
  let specsData = null;
  let selectedSpecPartName = null;
  let selectedAlternativeNames = new Set();

  async function loadSpecsStudio() {
    const card = document.getElementById('specs-studio-card');
    if (!card) return;

    const compSelect = document.getElementById('select-specs-component');
    const toggleContainer = document.getElementById('specs-compare-toggle-container');
    const altsPicker = document.getElementById('specs-alternatives-picker');
    const tableContainer = document.getElementById('specs-table-container');
    const missingContainer = document.getElementById('specs-missing-container');
    const missingText = document.getElementById('specs-missing-text');
    const badge = document.getElementById('specs-header-badge');

    try {
      if (tableContainer) {
        tableContainer.innerHTML = '<div class="caption text-muted" style="padding:16px;">Loading component datasheet specs...</div>';
      }
      const data = await api.getSpecs();
      specsData = data;

      const parts = data.parts || [];
      const without = data.without_specs || [];

      if (badge) {
        badge.textContent = `${parts.length} with specs`;
        badge.className = parts.length > 0 ? 'badge PASS' : 'badge WARN';
      }

      if (missingContainer && missingText) {
        if (without.length > 0) {
          missingContainer.style.display = 'block';
          missingText.textContent = `No typed specs for: ${without.slice(0, 8).join(', ')}${without.length > 8 ? ' ...' : ''}`;
        } else {
          missingContainer.style.display = 'none';
        }
      }

      if (!parts || parts.length === 0) {
        if (tableContainer) {
          tableContainer.innerHTML = '<div class="caption text-muted" style="padding:16px;">No component in this build has typed datasheet specs in the library.</div>';
        }
        if (compSelect) compSelect.innerHTML = '<option value="">(No parts with specs)</option>';
        if (toggleContainer) toggleContainer.style.display = 'none';
        if (altsPicker) altsPicker.style.display = 'none';
        return;
      }

      const previousChoice = selectedSpecPartName;
      if (compSelect) {
        compSelect.innerHTML = '';
        parts.forEach(p => {
          const opt = document.createElement('option');
          opt.value = p.name;
          opt.textContent = `${p.name} (${p.role || 'part'})`;
          compSelect.appendChild(opt);
        });

        if (previousChoice && parts.some(p => p.name === previousChoice)) {
          compSelect.value = previousChoice;
        } else {
          compSelect.value = parts[0].name;
        }
        selectedSpecPartName = compSelect.value;
      }

      await updateSpecsViewForPart();
    } catch (err) {
      console.warn('Specs studio error:', err);
      if (tableContainer) {
        tableContainer.innerHTML = `<div class="alert warning">Could not load specs: ${escapeHtml(err.message)}</div>`;
      }
    }
  }

  async function updateSpecsViewForPart() {
    if (!specsData || !specsData.parts) return;
    const currentPart = specsData.parts.find(p => p.name === selectedSpecPartName);
    if (!currentPart) return;

    const toggleContainer = document.getElementById('specs-compare-toggle-container');
    const toggleInput = document.getElementById('toggle-compare-specs');
    const toggleLabel = document.getElementById('label-compare-toggle');
    const altsPicker = document.getElementById('specs-alternatives-picker');
    const alts = currentPart.alternatives || [];
    const pluralName = currentPart.plural_role || 'parts';

    if (alts.length > 0) {
      if (toggleContainer) toggleContainer.style.display = 'flex';
      if (toggleLabel) toggleLabel.textContent = `Compare with other ${pluralName}`;

      const defaultPicks = alts.slice(0, 3).map(a => a.name);
      selectedAlternativeNames = new Set(defaultPicks);

      renderAlternativeChips(alts);
    } else {
      if (toggleContainer) toggleContainer.style.display = 'none';
      if (toggleInput) toggleInput.checked = false;
      if (altsPicker) altsPicker.style.display = 'none';
    }

    if (toggleInput && toggleInput.checked && alts.length > 0) {
      if (altsPicker) altsPicker.style.display = 'block';
      await renderSpecsComparisonTable(currentPart.name, Array.from(selectedAlternativeNames));
    } else {
      if (altsPicker) altsPicker.style.display = 'none';
      await renderSpecsComparisonTable(currentPart.name, []);
    }
  }

  function renderAlternativeChips(alts) {
    const altsChips = document.getElementById('specs-alternatives-chips');
    const altsCount = document.getElementById('specs-picker-count');
    if (!altsChips) return;

    altsChips.innerHTML = '';
    if (altsCount) altsCount.textContent = `${selectedAlternativeNames.size} of 4 selected`;

    alts.forEach(alt => {
      const isSelected = selectedAlternativeNames.has(alt.name);
      const chip = document.createElement('label');
      chip.className = `specs-chip ${isSelected ? 'active' : ''}`;

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = alt.name;
      cb.checked = isSelected;

      cb.addEventListener('change', async () => {
        if (cb.checked) {
          if (selectedAlternativeNames.size >= 4) {
            cb.checked = false;
            alert('You can compare up to 4 alternatives at once.');
            return;
          }
          selectedAlternativeNames.add(alt.name);
          chip.classList.add('active');
        } else {
          selectedAlternativeNames.delete(alt.name);
          chip.classList.remove('active');
        }
        if (altsCount) altsCount.textContent = `${selectedAlternativeNames.size} of 4 selected`;
        await renderSpecsComparisonTable(selectedSpecPartName, Array.from(selectedAlternativeNames));
      });

      const nameSpan = document.createElement('span');
      nameSpan.textContent = alt.name;

      const badge = document.createElement('span');
      badge.className = 'specs-chip-badge';
      const sharedCount = alt.shared_keys?.length || 0;
      badge.textContent = `${sharedCount} shared`;

      chip.appendChild(cb);
      chip.appendChild(nameSpan);
      chip.appendChild(badge);
      altsChips.appendChild(chip);
    });
  }

  async function renderSpecsComparisonTable(partName, compareWith = []) {
    const tableContainer = document.getElementById('specs-table-container');
    if (!tableContainer) return;

    try {
      tableContainer.innerHTML = '<div class="caption text-muted" style="padding:16px;">Updating specs table...</div>';
      const res = await api.compareSpecs(partName, compareWith);
      if (res && res.html) {
        tableContainer.innerHTML = res.html;
      } else {
        tableContainer.innerHTML = '<div class="caption text-muted" style="padding:16px;">No comparison table available.</div>';
      }
    } catch (err) {
      console.warn('Comparison error:', err);
      tableContainer.innerHTML = `<div class="alert warning">Comparison error: ${escapeHtml(err.message)}</div>`;
    }
  }

  // Specs event listeners
  document.getElementById('select-specs-component')?.addEventListener('change', async (e) => {
    selectedSpecPartName = e.target.value;
    await updateSpecsViewForPart();
  });

  document.getElementById('toggle-compare-specs')?.addEventListener('change', async (e) => {
    const altsPicker = document.getElementById('specs-alternatives-picker');
    if (!selectedSpecPartName) return;

    if (e.target.checked) {
      if (altsPicker) altsPicker.style.display = 'block';
      await renderSpecsComparisonTable(selectedSpecPartName, Array.from(selectedAlternativeNames));
    } else {
      if (altsPicker) altsPicker.style.display = 'none';
      await renderSpecsComparisonTable(selectedSpecPartName, []);
    }
  });

  document.getElementById('btn-fetch-missing-specs')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-fetch-missing-specs');
    const origText = btn ? btn.textContent : '';
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Fetching with Gemini...';
    }
    try {
      const res = await api.fetchMissingSpecs();
      alert(`Fetched missing specs (${res.fetched_count ?? 'done'}).`);
      await loadSpecsStudio();
    } catch (err) {
      alert(`Failed to fetch specs with Gemini: ${err.message}`);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = origText;
      }
    }
  });

  // ── 10. Evidence Ledger ──
  async function loadEvidenceLedger() {
    const titleText = document.getElementById('ledger-title-text');
    const tbody = document.getElementById('tbody-evidence-ledger');
    if (!titleText || !tbody) return;

    try {
      tbody.innerHTML = '<tr><td colspan="9" class="caption text-muted" style="padding:16px;text-align:center;">Loading evidence ledger...</td></tr>';
      const data = await api.getLedger();
      const rows = data.rows || [];
      const summary = data.summary || {};

      const verifiedCount = summary.verified || 0;
      const seedCount = summary.seed || 0;
      const unverifiedCount = summary.unverified || 0;
      const manualCount = summary.manual || 0;

      let summaryParts = [`Evidence ledger`, `${verifiedCount} verified`, `${seedCount} seed values`, `${unverifiedCount} unverified links`];
      if (manualCount > 0) {
        summaryParts.push(`${manualCount} user-entered`);
      }
      titleText.textContent = summaryParts.join(' · ');

      if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="caption text-muted" style="padding:16px;text-align:center;">No ledger rows recorded for this build.</td></tr>';
        return;
      }

      tbody.innerHTML = rows.map(r => {
        const status = r.status || '';
        let statusCls = 'status-seed';
        if (status === 'verified') statusCls = 'status-verified';
        else if (status === 'unverified') statusCls = 'status-unverified';
        else if (status === 'user-entered') statusCls = 'status-user-entered';

        const url = r.source_url || '';
        const urlDisplay = url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="ledger-url-link" title="${escapeHtml(url)}">${escapeHtml(url)}</a>` : '';

        return `
          <tr>
            <td style="font-family:var(--font-mono);font-size:0.78rem;color:var(--text-muted);">${escapeHtml(r.part_id || '')}</td>
            <td style="font-weight:600;">${escapeHtml(r.part || '')}</td>
            <td style="color:var(--text-secondary);">${escapeHtml(r.role || '')}</td>
            <td style="font-family:var(--font-mono);font-size:0.8rem;color:#A5B4FC;">${escapeHtml(r.field || '')}</td>
            <td style="font-weight:600;color:#F8FAFC;">${escapeHtml(String(r.value ?? ''))}</td>
            <td style="color:var(--text-muted);font-size:0.8rem;">${escapeHtml(r.unit || '')}</td>
            <td><span class="${statusCls}">${escapeHtml(status)}</span></td>
            <td style="color:var(--text-secondary);">${escapeHtml(r.source || '')}</td>
            <td>${urlDisplay}</td>
          </tr>
        `;
      }).join('');
    } catch (err) {
      console.warn('Evidence ledger load error:', err);
      tbody.innerHTML = `<tr><td colspan="9" class="alert warning" style="padding:12px;">Could not load evidence ledger: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  // ── 11. Research What This Project Needs (Build Guides) ──
  async function loadResearchGuides() {
    const card = document.getElementById('research-guides-card');
    if (!card) return;

    const btn = document.getElementById('btn-research-build-guides');
    const badge = document.getElementById('research-guides-badge');
    const statusText = document.getElementById('research-status-text');
    const resultsContainer = document.getElementById('research-results-container');

    try {
      const data = await api.getResearchGuides();
      if (!data.enabled) {
        if (btn) {
          btn.title = 'Requires TINYFISH_API_KEY and GEMINI_API_KEY in environment';
        }
        if (statusText) {
          statusText.textContent = data.enabled ? '' : '(Live search requires TINYFISH_API_KEY & GEMINI_API_KEY)';
        }
      }

      if (data.result && data.result.items && data.result.items.length > 0) {
        renderResearchResults(data.result);
      } else {
        if (badge) {
          badge.textContent = 'Ready';
          badge.className = 'badge UNKNOWN';
        }
        if (resultsContainer) resultsContainer.style.display = 'none';
      }
    } catch (err) {
      console.warn('Research guides load error:', err);
    }
  }

  function renderResearchResults(result) {
    const badge = document.getElementById('research-guides-badge');
    const resultsContainer = document.getElementById('research-results-container');
    const summaryCaption = document.getElementById('research-summary-caption');
    const itemsList = document.getElementById('research-items-list');
    const sourcesContainer = document.getElementById('research-guides-sources');
    const linksContainer = document.getElementById('research-guides-links');

    if (!result || !resultsContainer || !itemsList) return;

    const items = result.items || [];
    const guides = result.guides || [];
    const mapped = items.filter(i => i.role && i.role !== 'other');
    const unmapped = items.filter(i => !i.role || i.role === 'other');

    if (badge) {
      badge.textContent = `${items.length} parts found`;
      badge.className = 'badge PASS';
    }

    if (summaryCaption) {
      summaryCaption.textContent = `${items.length} components stated in ${guides.length} guides; ` +
        `${mapped.length} map to parts we can rule-check, ${unmapped.length} do not (shown as not covered).`;
    }

    // Guides links
    if (sourcesContainer && linksContainer) {
      if (guides.length > 0) {
        sourcesContainer.style.display = 'block';
        linksContainer.innerHTML = guides.map((u, i) => `
          <div>
            <span class="caption text-muted">${i + 1}.</span>
            <a href="${escapeHtml(u)}" target="_blank" rel="noopener noreferrer" class="package-item-link">${escapeHtml(u)} ↗</a>
          </div>
        `).join('');
      } else {
        sourcesContainer.style.display = 'none';
      }
    }

    // Component items
    itemsList.innerHTML = items.map(item => {
      const isMapped = item.role && item.role !== 'other';
      const icon = isMapped ? '✅' : '⚠️';
      const roleText = isMapped ? item.role : 'not covered by our catalog';
      const quote = (item.quote || '').slice(0, 160);
      const url = item.url || '';

      return `
        <div class="research-item-box">
          <div class="research-item-header">
            <span>${icon}</span>
            <span>${escapeHtml(item.name || '')}</span>
            <span class="caption text-muted">· ${escapeHtml(roleText)}</span>
          </div>
          ${quote ? `
            <div class="research-item-quote">
              “${escapeHtml(quote)}”
              ${url ? `— <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="research-item-url">${escapeHtml(url)}</a>` : ''}
            </div>
          ` : ''}
        </div>
      `;
    }).join('');

    resultsContainer.style.display = 'block';
  }

  // Click handler for Research Build Guides button
  document.getElementById('btn-research-build-guides')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-research-build-guides');
    const logBox = document.getElementById('research-log-box');
    const statusText = document.getElementById('research-status-text');
    const origText = btn ? btn.textContent : '';

    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Researching build guides...';
    }
    if (statusText) statusText.textContent = 'Searching web and extracting components with quotes...';
    if (logBox) {
      logBox.style.display = 'block';
      logBox.innerHTML = '<div class="log-entry">Connecting to TinyFish search engine & fetcher...</div>';
    }

    try {
      const res = await api.researchGuides();
      if (res && res.log && logBox) {
        logBox.innerHTML = res.log.map(l => `<div class="log-entry">${escapeHtml(l)}</div>`).join('');
      }
      if (res && res.result) {
        renderResearchResults(res.result);
        if (statusText) statusText.textContent = 'Research completed successfully.';
      }
    } catch (err) {
      console.warn('Research guides error:', err);
      if (logBox) {
        logBox.innerHTML += `<div class="log-entry error" style="color:#F87171;">Error: ${escapeHtml(err.message)}</div>`;
      }
      if (statusText) statusText.textContent = `Error: ${err.message}`;
      alert(`Research build guides error: ${err.message}`);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = origText;
      }
    }
  });

  // Resume Upload Trigger
  document.getElementById('btn-trigger-upload')?.addEventListener('click', () => {
    document.getElementById('resume-file-input').click();
  });

  document.getElementById('resume-file-input')?.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    document.getElementById('resume-status-label').textContent = `Uploading ${file.name}...`;
    try {
      const res = await api.uploadResume(file);
      document.getElementById('resume-status-label').textContent = `Read ${file.name}. Confirmed ${res.skills?.length || 0} factors.`;
    } catch (err) {
      document.getElementById('resume-status-label').textContent = `Upload error: ${err.message}`;
    }
  });
});
