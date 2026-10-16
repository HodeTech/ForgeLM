/**
 * ForgeLM site — in-browser YAML wizard.
 *
 * Mirrors the CLI ``forgelm --wizard`` flow but lives entirely in the
 * browser: walks the operator through 9 steps (welcome / use-case /
 * trainer / model / dataset / training / compliance / operations /
 * review) and emits a working ``quickstart-generated.yaml`` they can
 * copy, download, or paste into a shell command. Every transition +
 * every input change is persisted to ``localStorage`` so a refresh
 * resumes the same step. Behaviour summary:
 *
 * - Triggers: ``[data-wizard-open]`` clicks + the ``?wizard=open``
 *   URL parameter (set by the home-page Beginner-slide CTA).
 * - State: in-memory object mirrored to ``localStorage`` under
 *   ``forgelm.wizard.state``. Schema versioning via ``state.v``
 *   so a future shape change can clear stale snapshots.
 * - YAML preview: rebuilt on every state change; both desktop
 *   sidebar and mobile accordion bind to the same string.
 * - i18n: every string fetched via ``window.tr(key)`` (helper
 *   exposed by main.js); fallback chain is current locale → EN → key.
 * - Accessibility: role=dialog, aria-modal, focus trap, Esc to
 *   close, return-focus to trigger button on close, aria-live on
 *   the step pane so screen readers announce step transitions.
 * - Reduced-motion: detected once at init and used to skip step
 *   slide animations + progress-bar transitions.
 */
(function () {
  'use strict';

  /* ── Helpers (translation + DOM utilities) ────────────── */

  // tr() lives in js/_shared.js — single source of truth shared with
  // main.js, i18n.js, and guide.js. Local alias keeps the rest of the
  // file readable.
  var tr = (window.ForgeLMShared && window.ForgeLMShared.tr) || function (k) { return k; };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else if (k === 'html') node.innerHTML = v;
        else if (k === 'on') {
          Object.keys(v).forEach(function (evt) {
            node.addEventListener(evt, v[evt]);
          });
        } else if (k.startsWith('data-')) node.setAttribute(k, v);
        else if (k === 'i18nKey') node.setAttribute('data-i18n', v);
        else node.setAttribute(k, v);
      });
    }
    if (children) {
      (Array.isArray(children) ? children : [children]).forEach(function (c) {
        if (c == null) return;
        if (typeof c === 'string') node.appendChild(document.createTextNode(c));
        else node.appendChild(c);
      });
    }
    return node;
  }

  /* ── State + persistence ──────────────────────────────── */

  var STORAGE_KEY = 'forgelm.wizard.state';
  // Schema version 3 adds the explicit trainer-selection step + the
  // trainer-specific hyperparameter knobs (dpo_beta, simpo_*, kto_beta,
  // grpo_*) that the schema supports but the wizard previously hid.
  // v2 → v3: new fields don't break old saves (loadState merges over
  // defaults), but bumping the version reserves the right to drop
  // incompatible state shapes if needed in the future.
  //
  // This number is the in-browser wizard's resume-state version; it is
  // intentionally independent of the CLI wizard's
  // ``forgelm/wizard/_state.py::_STATE_VERSION`` (currently 1).  The
  // two surfaces persist to entirely different stores (browser
  // ``localStorage`` here vs. XDG cache JSON there) and bump on their
  // own state-shape schedules, so a drift between the two numbers is
  // normal and not a synchronisation bug.
  var STATE_VERSION = 3;

  // Trainer registry — single source of truth for the 6 trainer_type
  // values the schema accepts (TrainingConfig.trainer_type Literal in
  // forgelm/config.py). Keep ordered; ``isPreference`` controls which
  // dataset shape rendering applies, ``hasParam`` lists which extra
  // hyperparameters must surface in Step 5 + emitted to YAML.
  var TRAINERS = [
    { id: 'sft',   recommended: true,
      isPreference: false, isKto: false, isGrpo: false,
      params: [] },
    { id: 'dpo',   isPreference: true,  isKto: false, isGrpo: false,
      params: ['dpoBeta'] },
    { id: 'orpo',  isPreference: true,  isKto: false, isGrpo: false,
      params: ['orpoBeta'] },
    { id: 'simpo', isPreference: true,  isKto: false, isGrpo: false,
      params: ['simpoGamma', 'simpoBeta'] },
    { id: 'kto',   isPreference: false, isKto: true,  isGrpo: false,
      params: ['ktoBeta'] },
    { id: 'grpo',  isPreference: false, isKto: false, isGrpo: true,
      params: ['grpoNumGenerations', 'grpoMaxCompletionLength', 'grpoRewardModel'] }
  ];
  function trainerDef(id) {
    for (var i = 0; i < TRAINERS.length; i++) {
      if (TRAINERS[i].id === id) return TRAINERS[i];
    }
    return TRAINERS[0]; // fallback to SFT
  }

  // F1 (review-cycle 3): schema-derived defaults from
  // ``forgelm/wizard/_defaults.json`` (regenerated by
  // ``tools/generate_wizard_defaults.py``).  ``js/wizard_defaults.js``
  // exposes ``WIZARD_DEFAULTS`` on the global object (``globalThis``
  // with a ``window`` fallback for pre-ES2020 browsers) BEFORE this
  // script loads; ``wd(section, key, fallback)`` reads from it with a
  // hardcoded fallback so a missing JSON asset (developer running off
  // a stale checkout) doesn't break the wizard.
  function wd(section, key, fallback) {
    var _global = typeof globalThis !== 'undefined' ? globalThis : window;
    var defaults = _global.WIZARD_DEFAULTS || {};
    var block = defaults[section];
    if (!block || typeof block !== 'object') return fallback;
    var value = block[key];
    return (value === undefined || value === null) ? fallback : value;
  }

  function defaultState() {
    return {
      v: STATE_VERSION,
      step: 0,
      experience: 'beginner',  // 'beginner' | 'expert'
      detailsVisible: true,    // toggle for inline tutorial paragraphs
      useCase: null,           // 'customer-support' | 'code-assistant' | 'domain-expert' | 'grpo-math' | 'medical-qa-tr' | 'custom'
      // Trainer selection. Use-case picks a sensible preset, but the
      // operator can override on Step 3 (trainer step). All 6 schema
      // values reachable: sft / dpo / orpo / simpo / kto / grpo.
      trainerType: 'sft',
      model: 'meta-llama/Llama-3.1-8B-Instruct',
      modelPreset: 'llama3-8b',
      datasetKind: 'huggingface',  // 'huggingface' | 'local-jsonl' | 'local-pdf'
      datasetName: '',
      // ``data.format`` / ``data.prompt_field`` / ``data.response_field`` are
      // intentionally absent — DataConfig auto-detects the row shape from
      // the JSONL fields (``messages`` array → chat; ``prompt`` + ``response``
      // → instruction). Forcing them in the YAML would trigger
      // ``extra="forbid"`` on DataConfig.
      qlora: true,
      // PEFT variant. 'lora' (default standard LoRA), 'dora' (weight-
      // decomposed), 'pissa' (singular value initialised), 'rslora'
      // (rank-stabilised). Mutually exclusive with GaLore (next field).
      loraMethod: 'lora',
      // GaLore: full-parameter training via gradient projection — no
      // adapters. When true, lora.method emission becomes inert (the
      // schema requires the lora block but GaLore bypasses it at
      // runtime) and training.galore_enabled fires.
      galoreEnabled: false,
      // GaLore optimizer variant. Only consulted when galoreEnabled.
      // _8bit halves optimiser-state VRAM; _layerwise cuts peak VRAM
      // further by recomputing per-layer.
      galoreOptim: 'galore_adamw_8bit',
      // F1 (review-cycle 3): defaults pulled from
      // ``window.WIZARD_DEFAULTS`` (schema-derived JSON shipped as
      // ``js/wizard_defaults.js``).  Hardcoded fallbacks match the
      // schema today — they activate only when the JSON failed to
      // load (e.g. dev viewing a stale local copy).  The CI guard at
      // ``tools/check_wizard_defaults_sync.py`` rejects schema /
      // shipped-file drift at PR time.
      loraR: wd('lora', 'r', 8),
      loraAlpha: wd('lora', 'alpha', 16),
      epochs: wd('training', 'num_train_epochs', 3),
      // ``learning_rate`` arrives from JSON as a number (2e-5).  The
      // dropdown options are scientific-notation strings ('2e-5'), so
      // we need ``Number.toExponential()`` rather than ``String()``
      // (which would produce '0.00002' and fail to match any option).
      // Falls back to the literal '2e-5' string when the JSON is
      // missing entirely.
      learningRate: (function () {
        var v = wd('training', 'learning_rate', '2e-5');
        return typeof v === 'number' ? v.toExponential() : String(v);
      })(),
      batchSize: wd('training', 'per_device_train_batch_size', 4),
      maxLength: wd('model', 'max_length', 2048),
      gradientAccumulation: wd('training', 'gradient_accumulation_steps', 2),
      // Trainer-specific hyperparameters. Defaults mirror the schema
      // defaults in forgelm/config.py (TrainingConfig fields), so a
      // wizard-emitted YAML matches the runtime defaults when the
      // operator doesn't deviate. Each value is conditionally
      // surfaced + emitted based on state.trainerType.
      dpoBeta: 0.1,                  // schema default
      orpoBeta: 0.1,                 // schema default
      simpoGamma: 0.5,               // schema default
      simpoBeta: 2.0,                // schema default
      ktoBeta: 0.1,                  // schema default
      grpoNumGenerations: 4,         // schema default
      grpoMaxCompletionLength: 512,  // schema default
      grpoRewardModel: '',           // schema default is None; '' = omit from YAML
      // EU AI Act risk + evaluation toggles. Audit log + Annex IV export
      // are NOT YAML toggles — the audit log is always emitted, and the
      // Annex IV bundle is produced whenever ``compliance.*`` metadata is
      // populated. The "human approval" gate maps to
      // ``evaluation.require_human_approval`` (Article 14).
      riskClassification: 'limited-risk',
      autoRevert: true,
      maxAcceptableLoss: 2.0,
      safetyEval: true,
      humanApproval: false,
      // Optional Annex IV provider metadata (ComplianceMetadataConfig).
      // Empty defaults are valid; the wizard exposes them for high-risk
      // tiers where the EU AI Act requires named provider info.
      providerName: '',
      systemName: '',
      intendedPurpose: '',
      // Evaluation: benchmark (lm-evaluation-harness) + LLM Judge.
      // Both are nested under evaluation.* and gated by their own
      // ``enabled`` toggles. ``benchmarkTasks`` is newline-separated
      // (e.g. arc_easy / hellaswag / mmlu) and emitted as a YAML list.
      benchmarkEnabled: false,
      benchmarkTasks: 'arc_easy\nhellaswag\nmmlu',
      benchmarkMinScore: '',  // empty → schema default (None)
      judgeEnabled: false,
      judgeModel: 'gpt-4o',
      judgeApiKeyEnv: 'OPENAI_API_KEY',
      judgeMinScore: 5.0,
      // Article 9 risk assessment fields. ``risk_category`` mirrors
      // ``compliance.risk_classification`` so the two sibling tier
      // fields stay in lockstep — see ForgeConfig._risk_tiers in
      // forgelm/config.py for why both exist. ``foreseeableMisuse``
      // and ``mitigationMeasures`` accept newline-separated lists in
      // the textarea and are emitted as YAML arrays.
      intendedUse: '',
      foreseeableMisuse: '',
      mitigationMeasures: '',
      vulnerableGroupsConsidered: false,
      // Article 10 data governance — surfaces inside the dataset
      // step accordion. All fields are free-text apart from the two
      // booleans (personal data + DPIA).
      collectionMethod: '',
      annotationProcess: '',
      knownBiases: '',
      personalDataIncluded: false,
      dpiaCompleted: false,
      // Webhook (Slack/Teams/Discord) notifications. ``webhookEnabled``
      // gates the whole block — when off, the YAML omits the webhook
      // section entirely. When on, ``webhookUrl`` is required (validated
      // in validateStep). The URL accepts either a literal https value
      // OR ``env:VAR_NAME`` to source from the environment — buildYaml
      // branches on the prefix.
      webhookEnabled: false,
      webhookUrl: '',
      webhookNotifyStart: false,
      webhookNotifySuccess: true,
      webhookNotifyFailure: true,
      // External monitoring-plan metadata; opt-in for every risk tier.
      monitoringEnabled: false,
      monitoringEndpoint: '',
      metricsExport: 'none',  // 'none' | 'prometheus' | 'datadog' | 'custom_webhook'
      alertOnDrift: false,
      monitoringCheckIntervalHours: 24,
      // Synthetic data generation (teacher → student). Optional
      // pre-training stage; ``syntheticTeacherModel`` is the API
      // name or local path for the teacher.
      syntheticEnabled: false,
      syntheticTeacherModel: 'gpt-4o',
      syntheticSeedFile: '',
      syntheticApiKeyEnv: 'OPENAI_API_KEY',
      // GDPR Article 5+17 retention horizons. Defaults below match
      // forgelm/config.py RetentionConfig.
      retentionAuditLog: 1825,        // 5 years
      retentionStaging: 7,
      retentionEphemeral: 90,
      retentionRawDocuments: 90,
      // Policy enforcement: 'log_only' (default; logs to audit only),
      // 'warn_on_excess' (stderr warning), 'block_on_excess' (aborts
      // trainer with EXIT_EVAL_FAILURE). Schema Literal — must use
      // one of these three values.
      retentionEnforce: 'log_only',
      // Track which steps the operator tried to advance past while
      // failing required-field validation. The renderer reads this to
      // decide whether to paint inline error messages — so the FIRST
      // visit to a step is clean (no errors shown), and only after
      // hitting Next on an invalid step do the errors appear.
      attemptedSteps: []
    };
  }

  function loadState() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (!parsed || parsed.v !== STATE_VERSION) return null;
      // Merge with defaults so newly-added fields don't break old saves.
      var merged = defaultState();
      Object.keys(parsed).forEach(function (k) {
        if (Object.hasOwn(merged, k)) merged[k] = parsed[k];
      });
      // ``attemptedSteps`` is per-session — wiping it on every load
      // ensures the operator opens the wizard to a clean form, no
      // pre-painted validation errors from a prior session. The error
      // surface only fires once they hit Next on an invalid step.
      merged.attemptedSteps = [];
      return merged;
    } catch (e) {
      return null;
    }
  }

  // Track storage failures so the renderer can surface a banner.
  // We only flag once per modal session — repeated failures are
  // expected once the bucket is full and would otherwise spam the
  // user. ``window.ForgeLMWizardStorageWarn`` survives across saves
  // so a refresh of the wizard pane carries the warning forward.
  function saveState(state) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      /* Private mode + quota-exceeded both land here. In-memory state
         keeps the operator going within the current modal session;
         the banner tells them their progress isn't persisted. */
      window.ForgeLMWizardStorageWarn = true;
      try {
        var msg = (window.ForgeLMShared && window.ForgeLMShared.tr)
          ? window.ForgeLMShared.tr('wizard.storage.unavailable')
          : 'Browser storage is unavailable — your wizard progress will not survive a refresh.';
        var existing = document.querySelector('[data-wizard-storage-warn]');
        if (!existing) {
          var banner = document.createElement('div');
          banner.setAttribute('data-wizard-storage-warn', '');
          banner.setAttribute('role', 'status');
          banner.setAttribute('aria-live', 'polite');
          banner.className = 'wizard-storage-warn';
          banner.textContent = msg;
          var modal = document.querySelector('[data-wizard-modal]');
          if (modal) modal.appendChild(banner);
        }
      } catch (_) { /* defensive — never let the banner crash a save */ }
    }
  }

  function clearState() {
    try { window.localStorage.removeItem(STORAGE_KEY); } catch (e) { /* noop */ }
  }

  /* ── High-risk auto-coercion (EU AI Act Art. 9-17 obligations) */
  // When the operator picks ``high-risk`` or ``unacceptable`` on Step 6
  // we force-enable the human approval gate + Llama Guard safety eval —
  // those are not optional under Articles 9-17. The audit log is always
  // emitted regardless of YAML (no toggle) and the Annex IV bundle is
  // produced from the populated ``compliance.*`` metadata, so neither
  // appears here. Selecting back to a lower tier leaves the toggles
  // where the operator left them; we only override on the upward
  // transition.
  var STRICT_RISK_TIERS = ['high-risk', 'unacceptable'];
  function isStrictRisk(state) {
    return STRICT_RISK_TIERS.indexOf(state.riskClassification) !== -1;
  }
  function coerceForRisk(state) {
    if (isStrictRisk(state)) {
      state.humanApproval = true;
      state.safetyEval = true;
    }
  }

  // Shared ASCII env-reference syntax for monitoring metadata and webhook secrets.
  function parseEnvRef(value) {
    var raw = (value || '').trim();
    if (raw.slice(0, 4).toLowerCase() !== 'env:') return { literal: raw };
    var name = raw.slice(4).trim();
    return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) ? { name: name } : { invalid: true };
  }

  function validMonitoringUrl(value) {
    if (!value) return true;
    var lower = value.toLowerCase();
    if (!lower.startsWith('http://') && !lower.startsWith('https://')) return false;
    for (var i = 0; i < value.length; i++) {
      if (value.charCodeAt(i) <= 32 || value.charCodeAt(i) === 127 || value[i] === '\\') return false;
    }
    try {
      return !!new URL(value).hostname;
    } catch (e) {
      return false;
    }
  }

  /* ── Required-field validation ────────────────────────── */
  // Some steps require operator input that has no sensible default —
  // e.g. a custom HuggingFace model ID, or a dataset path / Hub name.
  // ``validateStep(state, stepId)`` returns an array of {field, key}
  // entries, one per validation failure. Empty array = step is valid.
  // The renderer for each step calls back into this function and, if
  // ``state.attemptedSteps`` includes the current step ID, paints
  // inline errors next to the offending input. ``goNext()`` blocks
  // advancement when validateStep returns a non-empty array.
  function validateStep(state, stepId) {
    var errors = [];
    var blank = function (v) { return !((v || '').trim()); };
    var strict = isStrictRisk(state);

    if (stepId === 'model') {
      if (state.modelPreset === 'custom') {
        var v = (state.model || '').trim();
        if (!v) {
          errors.push({ field: 'model', key: 'wizard.error.model.required' });
        } else if (v.indexOf('/') < 0) {
          // HF Hub IDs are <org>/<repo>; bare names won't resolve.
          errors.push({ field: 'model', key: 'wizard.error.model.format' });
        }
      }
    } else if (stepId === 'dataset') {
      if (blank(state.datasetName)) {
        errors.push({ field: 'datasetName', key: 'wizard.error.dataset.required' });
      }
      // Article 10 (data governance) is mandatory documentation for
      // high-risk + unacceptable systems. The wizard treats the three
      // free-text fields as required when the operator picked a
      // strict tier on the compliance step.
      if (strict) {
        if (blank(state.collectionMethod)) {
          errors.push({ field: 'collectionMethod', key: 'wizard.error.governance.collection_method.required' });
        }
        if (blank(state.annotationProcess)) {
          errors.push({ field: 'annotationProcess', key: 'wizard.error.governance.annotation_process.required' });
        }
        if (blank(state.knownBiases)) {
          errors.push({ field: 'knownBiases', key: 'wizard.error.governance.known_biases.required' });
        }
      }
    } else if (stepId === 'compliance') {
      // High-risk + unacceptable tiers carry mandatory Annex IV §1
      // metadata + Article 9 risk-management evidence. Without these
      // the runtime accepts empty strings (schema defaults), but the
      // resulting compliance bundle would fail an external audit, so
      // we enforce the obligation here.
      if (strict) {
        if (blank(state.providerName)) {
          errors.push({ field: 'providerName', key: 'wizard.error.compliance.provider_name.required' });
        }
        if (blank(state.systemName)) {
          errors.push({ field: 'systemName', key: 'wizard.error.compliance.system_name.required' });
        }
        if (blank(state.intendedPurpose)) {
          errors.push({ field: 'intendedPurpose', key: 'wizard.error.compliance.intended_purpose.required' });
        }
        if (blank(state.intendedUse)) {
          errors.push({ field: 'intendedUse', key: 'wizard.error.risk_assessment.intended_use.required' });
        }
        if (blank(state.foreseeableMisuse)) {
          errors.push({ field: 'foreseeableMisuse', key: 'wizard.error.risk_assessment.foreseeable_misuse.required' });
        }
        if (blank(state.mitigationMeasures)) {
          errors.push({ field: 'mitigationMeasures', key: 'wizard.error.risk_assessment.mitigation_measures.required' });
        }
      }
      // Eval-gate accordion: each gate's primary input is required
      // when the gate is enabled. ``benchmarkTasks`` is a newline list
      // — empty after trim means "no tasks", which makes the gate a
      // no-op. Same for the judge API key env var.
      if (state.benchmarkEnabled && blank(state.benchmarkTasks)) {
        errors.push({ field: 'benchmarkTasks', key: 'wizard.error.benchmark.tasks.required' });
      }
      if (state.judgeEnabled && blank(state.judgeApiKeyEnv)) {
        errors.push({ field: 'judgeApiKeyEnv', key: 'wizard.error.judge.api_key_env.required' });
      }
    } else if (stepId === 'operations') {
      // Each accordion that exposes a primary input gates that input
      // as required ONLY when the accordion's own enable toggle is on.
      // Off → skip; the operator opted out of the whole feature.
      if (state.webhookEnabled && blank(state.webhookUrl)) {
        errors.push({ field: 'webhookUrl', key: 'wizard.error.webhook.required' });
      } else if (state.webhookEnabled && parseEnvRef(state.webhookUrl).invalid) {
        errors.push({ field: 'webhookUrl', key: 'wizard.error.env_ref.invalid' });
      }
      if (state.monitoringEnabled && parseEnvRef(state.monitoringEndpoint).invalid) {
        errors.push({ field: 'monitoringEndpoint', key: 'wizard.error.env_ref.invalid' });
      } else if (state.monitoringEnabled && !validMonitoringUrl(parseEnvRef(state.monitoringEndpoint).literal)) {
        errors.push({ field: 'monitoringEndpoint', key: 'wizard.error.monitoring.endpoint.invalid' });
      }
      if (state.monitoringEnabled && (!Number.isInteger(state.monitoringCheckIntervalHours) ||
          state.monitoringCheckIntervalHours < 1 || state.monitoringCheckIntervalHours > 720)) {
        errors.push({ field: 'monitoringCheckIntervalHours', key: 'wizard.error.monitoring.interval.invalid' });
      }
      if (state.syntheticEnabled) {
        if (blank(state.syntheticTeacherModel)) {
          errors.push({ field: 'syntheticTeacherModel', key: 'wizard.error.synthetic.teacher_model.required' });
        }
        if (blank(state.syntheticSeedFile)) {
          errors.push({ field: 'syntheticSeedFile', key: 'wizard.error.synthetic.seed_file.required' });
        }
        if (blank(state.syntheticApiKeyEnv)) {
          errors.push({ field: 'syntheticApiKeyEnv', key: 'wizard.error.synthetic.api_key_env.required' });
        }
      }
    }
    return errors;
  }

  function fieldHasError(state, stepId, fieldName) {
    if (!state.attemptedSteps || state.attemptedSteps.indexOf(stepId) < 0) return null;
    var errs = validateStep(state, stepId);
    for (var i = 0; i < errs.length; i++) {
      if (errs[i].field === fieldName) return errs[i].key;
    }
    return null;
  }

  /* ── Use-case templates (Step 2 → preselect Step 3-5) ─── */

  // Use-case → state preset. ``trainerType`` is included so the new
  // trainer step (Step 3) lands on the right card by default; the
  // operator can still override it. The mapping is aligned with the
  // shipped templates under ``forgelm/templates/`` — every shipped
  // template uses ``trainer_type: sft`` except ``grpo-math`` which
  // uses ``grpo``. The wizard previously emitted DPO/ORPO for
  // customer-support / code-assistant which contradicted the templates;
  // this file now matches the runtime defaults.  Use-case keys
  // (``code-assistant``, ``medical-qa-tr``, …) are kept in lockstep
  // with ``forgelm/quickstart.py::TEMPLATES`` — the CLI quickstart
  // catalogue is the single source of truth so an operator who
  // crosses surfaces never sees a renamed key.
  // The model + dataset values mirror ``forgelm/quickstart.py::TEMPLATES``
  // verbatim — that registry is the runtime source of truth, and an operator
  // who finishes the wizard and runs ``forgelm --config <generated>.yaml``
  // must see exactly the same defaults the CLI quickstart would have set.
  // Bundled-dataset templates use ``datasetKind: 'local-jsonl'`` with the
  // in-repo path under ``forgelm/templates/<key>/data.jsonl`` (the trainer
  // resolves the path without any HF Hub call).  ``domain-expert`` is the
  // only BYOD template — kind ``local-pdf`` reflects its README's "drop
  // your PDFs into ./policies/".
  var USE_CASE_PRESETS = {
    'customer-support': {
      trainerType: 'sft',
      model: 'Qwen/Qwen2.5-7B-Instruct',
      modelPreset: 'qwen-7b',
      datasetKind: 'local-jsonl',
      datasetName: 'forgelm/templates/customer-support/data.jsonl'
    },
    'code-assistant': {
      trainerType: 'sft',
      model: 'Qwen/Qwen2.5-Coder-7B-Instruct',
      modelPreset: 'custom',
      datasetKind: 'local-jsonl',
      datasetName: 'forgelm/templates/code-assistant/data.jsonl'
    },
    'domain-expert': {
      trainerType: 'sft',
      model: 'Qwen/Qwen2.5-7B-Instruct',
      modelPreset: 'qwen-7b',
      datasetKind: 'local-pdf',
      datasetName: './policies/'
    },
    'grpo-math': {
      trainerType: 'grpo',
      model: 'Qwen/Qwen2.5-Math-7B-Instruct',
      modelPreset: 'custom',
      datasetKind: 'local-jsonl',
      datasetName: 'forgelm/templates/grpo-math/data.jsonl'
    },
    'medical-qa-tr': {
      trainerType: 'sft',
      model: 'Qwen/Qwen2.5-7B-Instruct',
      modelPreset: 'qwen-7b',
      datasetKind: 'local-jsonl',
      datasetName: 'forgelm/templates/medical-qa-tr/data.jsonl'
    },
    'custom': {
      /* leave the existing values intact — user wants explicit control */
    }
  };

  function applyUseCasePreset(state, useCase) {
    var preset = USE_CASE_PRESETS[useCase] || {};
    Object.keys(preset).forEach(function (k) { state[k] = preset[k]; });
    state.useCase = useCase;
  }

  /* ── YAML serialisation ───────────────────────────────── */

  // Build a syntax-highlighted YAML string. We intentionally avoid a
  // YAML library — the schema we emit is small enough that a manual
  // line-builder is auditable + dependency-free. The output is
  // structurally aligned with ``forgelm/config.py`` (Pydantic models
  // with ``extra="forbid"`` on every block), so it passes
  // ``forgelm --config quickstart-generated.yaml --dry-run`` cleanly.
  //
  // Audit notes (deliberately NOT emitted, despite operator intuition):
  //
  // * ``data.format`` / ``data.prompt_field`` / ``data.response_field``
  //   — DataConfig auto-detects the row shape from the JSONL fields
  //   themselves; emitting these triggers ``extra="forbid"``.
  // * ``compliance.audit_log`` / ``compliance.annex_iv_export`` —
  //   ComplianceMetadataConfig has no such toggles. The audit log is
  //   always written; the Annex IV bundle is produced whenever the
  //   ``compliance.*`` metadata block is present.
  // * Human-approval gate lives at ``evaluation.require_human_approval``
  //   (Article 14), not under ``compliance``.
  // * ``evaluation.safety.classifier`` (not ``classifier_path``).
  function buildYaml(state) {
    var lines = [];
    function comment(s) { lines.push('# ' + s); }
    function blank() { lines.push(''); }
    function k(indent, key, value) {
      var pad = '';
      for (var i = 0; i < indent; i++) pad += '  ';
      lines.push(pad + key + ': ' + value);
    }
    function header(indent, key) {
      var pad = '';
      for (var i = 0; i < indent; i++) pad += '  ';
      lines.push(pad + key + ':');
    }

    comment('Generated by the ForgeLM site wizard.');
    comment('Validate: forgelm --config quickstart-generated.yaml --dry-run');
    blank();

    header(0, 'model');
    k(1, 'name_or_path', JSON.stringify(state.model));
    k(1, 'load_in_4bit', state.qlora ? 'true' : 'false');
    if (state.qlora) {
      k(1, 'bnb_4bit_quant_type', '"nf4"');
      k(1, 'bnb_4bit_compute_dtype', '"auto"');
    }
    // P3: emit ``max_length`` so the YAML matches the operator's
    // chosen sequence length rather than defaulting to 2048 silently.
    // Always emitted even when equal to the schema default — keeps
    // long-context configs self-documenting.
    k(1, 'max_length', String(state.maxLength || 2048));
    blank();

    header(0, 'lora');
    k(1, 'r', String(state.loraR));
    k(1, 'alpha', String(state.loraAlpha));
    k(1, 'dropout', '0.1');  // schema default
    k(1, 'bias', '"none"');
    // ``method`` is emitted whenever the operator picked a non-default
    // PEFT variant. GaLore mode also emits the lora block (schema
    // requires it) but with the standard LoRA method as a no-op default
    // — GaLore bypasses LoRA at runtime regardless of what's here.
    if (!state.galoreEnabled && state.loraMethod && state.loraMethod !== 'lora') {
      k(1, 'method', JSON.stringify(state.loraMethod));
    }
    k(1, 'target_modules', '["q_proj", "v_proj"]');
    blank();

    header(0, 'data');
    if (state.datasetKind === 'local-pdf') {
      comment(' PDFs need ingestion first — run before training:');
      comment('   forgelm ingest ' + (state.datasetName || './documents/') + ' --recursive --output data/corpus.jsonl');
      k(1, 'dataset_name_or_path', '"./data/corpus.jsonl"');
    } else {
      var defaultPath = state.datasetKind === 'huggingface'
        ? 'org/dataset-name'
        : './data/train.jsonl';
      k(1, 'dataset_name_or_path', JSON.stringify(state.datasetName || defaultPath));
    }
    // Trainer-aware autodetect note — different alignment paradigms
    // expect different row shapes. SFT auto-detects messages or
    // prompt+response; preference trainers (DPO/ORPO/SimPO) need
    // chosen+rejected pairs; KTO needs binary feedback; GRPO needs
    // a prompt and (optionally) a gold answer.
    var trDef = trainerDef(state.trainerType);
    if (trDef.isPreference) {
      comment(' ' + state.trainerType.toUpperCase() + ' expects preference rows: { "prompt": ..., "chosen": ..., "rejected": ... }');
    } else if (trDef.isKto) {
      comment(' KTO expects binary-feedback rows: { "prompt": ..., "completion": ..., "label": true|false }');
    } else if (trDef.isGrpo) {
      comment(' GRPO expects rollout prompts: { "prompt": ..., "gold_answer": "..." }');
      comment(' (gold_answer is optional; required only when using the regex correctness reward.)');
    } else {
      comment(' DataConfig auto-detects SFT row shape: { "messages": [...] } → chat;');
      comment(' { "prompt": ..., "response": ... } → instruction-following.');
    }
    // Article 10 data governance — emitted as a nested ``governance``
    // block under data when the operator filled in any field.
    var hasGovernance = state.collectionMethod || state.annotationProcess || state.knownBiases || state.personalDataIncluded || state.dpiaCompleted;
    if (hasGovernance) {
      header(1, 'governance');
      if (state.collectionMethod) k(2, 'collection_method', JSON.stringify(state.collectionMethod));
      if (state.annotationProcess) k(2, 'annotation_process', JSON.stringify(state.annotationProcess));
      if (state.knownBiases) k(2, 'known_biases', JSON.stringify(state.knownBiases));
      if (state.personalDataIncluded) k(2, 'personal_data_included', 'true');
      if (state.dpiaCompleted) k(2, 'dpia_completed', 'true');
    }
    blank();

    header(0, 'training');
    k(1, 'trainer_type', JSON.stringify(state.trainerType));
    k(1, 'output_dir', '"./checkpoints"');
    k(1, 'final_model_dir', '"final_model"');
    k(1, 'num_train_epochs', String(state.epochs));
    k(1, 'learning_rate', state.learningRate);
    k(1, 'per_device_train_batch_size', String(state.batchSize));
    k(1, 'gradient_accumulation_steps', String(state.gradientAccumulation));
    k(1, 'merge_adapters', 'false');
    // GaLore: full-parameter training via gradient projection,
    // alternative to LoRA. When enabled the lora.* block above is
    // ignored at runtime; we still emit it because the schema requires
    // the block to be present.
    if (state.galoreEnabled) {
      k(1, 'galore_enabled', 'true');
      k(1, 'galore_optim', JSON.stringify(state.galoreOptim));
    }
    // Trainer-specific hyperparameters — only emit fields whose state
    // value differs from the schema default OR when the operator is
    // explicitly tuning that trainer (so the YAML carries an audit
    // trail of what the wizard chose). For GRPO, ``grpo_reward_model``
    // stays out of the YAML when empty (schema default is None — TRL
    // then wires the built-in format/length reward).
    if (state.trainerType === 'dpo') {
      k(1, 'dpo_beta', String(state.dpoBeta));
    } else if (state.trainerType === 'orpo') {
      k(1, 'orpo_beta', String(state.orpoBeta));
    } else if (state.trainerType === 'simpo') {
      k(1, 'simpo_gamma', String(state.simpoGamma));
      k(1, 'simpo_beta', String(state.simpoBeta));
    } else if (state.trainerType === 'kto') {
      k(1, 'kto_beta', String(state.ktoBeta));
    } else if (state.trainerType === 'grpo') {
      k(1, 'grpo_num_generations', String(state.grpoNumGenerations));
      k(1, 'grpo_max_completion_length', String(state.grpoMaxCompletionLength));
      if (state.grpoRewardModel) {
        k(1, 'grpo_reward_model', JSON.stringify(state.grpoRewardModel));
      }
    }
    blank();

    header(0, 'evaluation');
    k(1, 'auto_revert', state.autoRevert ? 'true' : 'false');
    if (state.autoRevert) {
      k(1, 'max_acceptable_loss', String(state.maxAcceptableLoss));
    }
    // Article 14: require_human_approval stages the model under
    // ``final_model.staging.<run_id>/`` and exits 4. The CLI then
    // expects ``forgelm approve <run_id>`` before promoting to
    // ``final_model/``.
    if (state.humanApproval) {
      k(1, 'require_human_approval', 'true');
    }
    if (state.safetyEval) {
      header(1, 'safety');
      k(2, 'enabled', 'true');
      k(2, 'classifier', '"meta-llama/Llama-Guard-3-8B"');
      k(2, 'min_classifier_confidence', '0.7');
      k(2, 'max_safety_regression', '0.05');
      // P1 / P18 (review-cycle 2): align safety-block surface with the
      // CLI wizard.  ``scoring`` and ``test_prompts`` were CLI-only;
      // surfacing them here lets the YAML carry the audit-policy
      // knobs (severity_thresholds) the schema supports.  Defaults
      // mirror ``forgelm/config.py`` SafetyConfig.
      k(2, 'scoring', '"binary"');
      k(2, 'track_categories', 'true');
      // ``severity_thresholds`` is a nested mapping — emit inline.
      lines.push('    severity_thresholds:');
      lines.push('      critical: 0');
      lines.push('      high: 0.01');
      lines.push('      medium: 0.05');
    }
    // BenchmarkConfig (lm-evaluation-harness). Optional quality gate
    // that scores the fine-tuned model on academic tasks. Tasks are
    // emitted as a YAML list; min_score is omitted when blank so the
    // schema default (None) applies.
    if (state.benchmarkEnabled) {
      header(1, 'benchmark');
      k(2, 'enabled', 'true');
      var benchTasks = (state.benchmarkTasks || '').split('\n')
        .map(function (s) { return s.trim(); })
        .filter(function (s) { return s.length > 0; });
      if (benchTasks.length) {
        lines.push('    tasks:');
        benchTasks.forEach(function (t) { lines.push('      - ' + JSON.stringify(t)); });
      }
      var ms = parseFloat(state.benchmarkMinScore);
      if (!isNaN(ms) && ms > 0) {
        k(2, 'min_score', String(ms));
      }
    }
    // JudgeConfig (LLM-as-Judge). Optional gate that scores outputs
    // with a stronger model (e.g. GPT-4o) and blocks promotion if
    // average score falls below min_score.
    if (state.judgeEnabled) {
      header(1, 'llm_judge');
      k(2, 'enabled', 'true');
      k(2, 'judge_model', JSON.stringify(state.judgeModel || 'gpt-4o'));
      if (state.judgeApiKeyEnv) {
        k(2, 'judge_api_key_env', JSON.stringify(state.judgeApiKeyEnv));
      }
      k(2, 'min_score', String(state.judgeMinScore));
    }
    blank();

    // ComplianceMetadataConfig — Annex IV §1 provider + system info.
    // Always emit risk_classification; provider/system fields stay
    // empty unless the operator filled them in (high-risk tiers
    // surface the inputs in Step 6).
    header(0, 'compliance');
    k(1, 'risk_classification', JSON.stringify(state.riskClassification));
    if (state.providerName) k(1, 'provider_name', JSON.stringify(state.providerName));
    if (state.systemName) k(1, 'system_name', JSON.stringify(state.systemName));
    if (state.intendedPurpose) k(1, 'intended_purpose', JSON.stringify(state.intendedPurpose));
    blank();

    // Article 9 risk assessment block. Only emit when the operator
    // filled in at least one field (or when the tier is strict, in
    // which case Article 9 documentation is mandatory). The
    // risk_category mirrors compliance.risk_classification so the
    // two sibling fields don't drift.
    function listFromTextarea(text) {
      if (!text) return [];
      return text.split('\n').map(function (s) { return s.trim(); }).filter(function (s) { return s.length > 0; });
    }
    var raMisuse = listFromTextarea(state.foreseeableMisuse);
    var raMitigation = listFromTextarea(state.mitigationMeasures);
    var hasRiskAssessment = state.intendedUse || raMisuse.length || raMitigation.length || state.vulnerableGroupsConsidered || isStrictRisk(state);
    if (hasRiskAssessment) {
      header(0, 'risk_assessment');
      k(1, 'risk_category', JSON.stringify(state.riskClassification));
      if (state.intendedUse) k(1, 'intended_use', JSON.stringify(state.intendedUse));
      if (raMisuse.length) {
        lines.push('  foreseeable_misuse:');
        raMisuse.forEach(function (m) { lines.push('    - ' + JSON.stringify(m)); });
      }
      if (raMitigation.length) {
        lines.push('  mitigation_measures:');
        raMitigation.forEach(function (m) { lines.push('    - ' + JSON.stringify(m)); });
      }
      if (state.vulnerableGroupsConsidered) k(1, 'vulnerable_groups_considered', 'true');
      blank();
    }

    // Webhook (Slack/Teams/Discord notifications). The URL field
    // accepts either a literal https URL or ``env:VAR_NAME`` to
    // source from the environment — the latter is the production
    // path because YAML files are version-controlled and the URL
    // is a secret.
    if (state.webhookEnabled && state.webhookUrl) {
      header(0, 'webhook');
      var webhookRef = parseEnvRef(state.webhookUrl);
      if (webhookRef.name) {
        k(1, 'url_env', JSON.stringify(webhookRef.name));
      } else if (!webhookRef.invalid) {
        k(1, 'url', JSON.stringify(webhookRef.literal));
      }
      if (state.webhookNotifyStart)   k(1, 'notify_on_start', 'true');
      if (!state.webhookNotifySuccess) k(1, 'notify_on_success', 'false');
      if (!state.webhookNotifyFailure) k(1, 'notify_on_failure', 'false');
      blank();
    }

    // Record an external plan without activating monitoring hooks.
    if (state.monitoringEnabled) {
      comment('External monitoring plan metadata only; ForgeLM runs no exporters, drift alerts, or scheduled checks.');
      header(0, 'monitoring');
      k(1, 'enabled', 'true');
      if (state.monitoringEndpoint) {
        var monitoringRef = parseEnvRef(state.monitoringEndpoint);
        if (monitoringRef.name) {
          k(1, 'endpoint_env', JSON.stringify(monitoringRef.name));
        } else if (!monitoringRef.invalid && monitoringRef.literal && validMonitoringUrl(monitoringRef.literal)) {
          k(1, 'endpoint', JSON.stringify(monitoringRef.literal));
        }
      }
      k(1, 'metrics_export', JSON.stringify(state.metricsExport || 'none'));
      k(1, 'alert_on_drift', state.alertOnDrift ? 'true' : 'false');
      if (Number.isInteger(state.monitoringCheckIntervalHours) && state.monitoringCheckIntervalHours >= 1 &&
          state.monitoringCheckIntervalHours <= 720) {
        k(1, 'check_interval_hours', String(state.monitoringCheckIntervalHours));
      }
      blank();
    }

    // Synthetic data generation (teacher → student distillation).
    // Only emitted when the operator opted in.
    if (state.syntheticEnabled) {
      header(0, 'synthetic');
      k(1, 'enabled', 'true');
      k(1, 'teacher_model', JSON.stringify(state.syntheticTeacherModel || 'gpt-4o'));
      if (state.syntheticSeedFile) k(1, 'seed_file', JSON.stringify(state.syntheticSeedFile));
      if (state.syntheticApiKeyEnv) k(1, 'api_key_env', JSON.stringify(state.syntheticApiKeyEnv));
      blank();
    }

    // GDPR Article 5(1)(e) + 17 retention horizons. Only emit fields
    // that deviate from schema defaults (1825 / 7 / 90 / 90) so the
    // YAML stays terse for the typical case.
    var retainDefaults = { audit: 1825, staging: 7, ephemeral: 90, raw: 90 };
    var nonDefaultRetention =
      state.retentionAuditLog !== retainDefaults.audit ||
      state.retentionStaging !== retainDefaults.staging ||
      state.retentionEphemeral !== retainDefaults.ephemeral ||
      state.retentionRawDocuments !== retainDefaults.raw ||
      (state.retentionEnforce && state.retentionEnforce !== 'log_only');
    if (nonDefaultRetention) {
      header(0, 'retention');
      if (state.retentionAuditLog !== retainDefaults.audit)
        k(1, 'audit_log_retention_days', String(state.retentionAuditLog));
      if (state.retentionStaging !== retainDefaults.staging)
        k(1, 'staging_ttl_days', String(state.retentionStaging));
      if (state.retentionEphemeral !== retainDefaults.ephemeral)
        k(1, 'ephemeral_artefact_retention_days', String(state.retentionEphemeral));
      if (state.retentionRawDocuments !== retainDefaults.raw)
        k(1, 'raw_documents_retention_days', String(state.retentionRawDocuments));
      // Schema default for enforce is 'log_only'; only emit when the
      // operator picked a stricter mode.
      if (state.retentionEnforce && state.retentionEnforce !== 'log_only') {
        k(1, 'enforce', JSON.stringify(state.retentionEnforce));
      }
      blank();
    }

    // Friendly closing comment so an operator reading the YAML cold
    // knows the audit log + Annex IV bundle land on disk regardless
    // of whether they show up as toggles here.
    comment(' Audit log (audit_log.jsonl) is always emitted — no YAML toggle.');
    comment(' Annex IV bundle is produced from the compliance.* metadata above.');

    return lines.join('\n');
  }

  // Lightweight YAML syntax tinting: paint comments + keys + strings
  // + booleans + numbers in different colours. The output is HTML so
  // it lives inside the ``<code>`` element directly — no heavy
  // tokeniser, just a series of targeted replacements with HTML
  // escaping done first to avoid XSS. Shared escape helper from
  // js/_shared.js with a local fallback for paranoia.
  var escapeHtml = (window.ForgeLMShared && window.ForgeLMShared.escapeHtml) ||
    function (s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    };
  function tintYaml(yaml) {
    var lines = yaml.split('\n');
    return lines.map(function (line) {
      var trimmed = line.replace(/^\s*/, '');
      if (trimmed.startsWith('#')) {
        return '<span class="yaml-comment">' + escapeHtml(line) + '</span>';
      }
      // key: value match — split on the first colon
      var match = line.match(/^(\s*)([A-Za-z_][A-Za-z0-9_]*)(:)(\s*)(.*)$/);
      if (!match) return escapeHtml(line);
      var indent = match[1];
      var key = match[2];
      var rest = match[5];
      var tinted;
      if (rest === '') {
        tinted = '';
      } else if (rest === 'true' || rest === 'false') {
        tinted = '<span class="yaml-bool">' + escapeHtml(rest) + '</span>';
      } else if (rest === 'null' || rest === '~') {
        tinted = '<span class="yaml-null">' + escapeHtml(rest) + '</span>';
      } else if (/^-?\d+(\.\d+)?(e-?\d+)?$/i.test(rest)) {
        tinted = '<span class="yaml-number">' + escapeHtml(rest) + '</span>';
      } else if (rest.startsWith('"') || rest.startsWith("'")) {
        tinted = '<span class="yaml-string">' + escapeHtml(rest) + '</span>';
      } else if (rest.startsWith('[')) {
        // Inline list — colour quoted entries as strings.
        tinted = escapeHtml(rest).replace(
          /(&quot;[^&]*?&quot;)/g,
          '<span class="yaml-string">$1</span>'
        );
      } else {
        tinted = escapeHtml(rest);
      }
      return escapeHtml(indent) + '<span class="yaml-key">' + escapeHtml(key) + '</span><span>:</span>' +
        (rest === '' ? '' : ' ' + tinted);
    }).join('\n');
  }

  /* ── Step definitions ─────────────────────────────────── */

  var STEPS = [
    { id: 'welcome',    titleKey: 'wizard.step.welcome.title',     nameKey: 'wizard.step.welcome.name' },
    { id: 'use-case',   titleKey: 'wizard.step.usecase.title',     nameKey: 'wizard.step.usecase.name' },
    { id: 'trainer',    titleKey: 'wizard.step.trainer.title',     nameKey: 'wizard.step.trainer.name' },
    { id: 'model',      titleKey: 'wizard.step.model.title',       nameKey: 'wizard.step.model.name' },
    { id: 'dataset',    titleKey: 'wizard.step.dataset.title',     nameKey: 'wizard.step.dataset.name' },
    { id: 'training',   titleKey: 'wizard.step.training.title',    nameKey: 'wizard.step.training.name' },
    { id: 'compliance', titleKey: 'wizard.step.compliance.title',  nameKey: 'wizard.step.compliance.name' },
    { id: 'operations', titleKey: 'wizard.step.operations.title',  nameKey: 'wizard.step.operations.name' },
    { id: 'review',     titleKey: 'wizard.step.review.title',      nameKey: 'wizard.step.review.name' }
  ];

  /* ── Wizard controller ────────────────────────────────── */

  function initWizard() {
    var modal = document.querySelector('[data-wizard-modal]');
    if (!modal) return;
    var dialog = modal.querySelector('.wizard-dialog');
    var pane = modal.querySelector('[data-wizard-pane]');
    var yamlOutput = modal.querySelector('[data-wizard-yaml-output]');
    var yamlPane = modal.querySelector('[data-wizard-yaml-pane]');
    var yamlToggle = modal.querySelector('[data-wizard-yaml-toggle]');
    var yamlCopy = modal.querySelector('[data-wizard-yaml-copy]');
    var progress = modal.querySelector('[data-wizard-progress]');
    var counterCurrent = modal.querySelector('[data-wizard-counter-current]');
    var counterTotal = modal.querySelector('[data-wizard-counter-total]');
    var stepName = modal.querySelector('[data-wizard-step-name]');
    var prevBtn = modal.querySelector('[data-wizard-prev]');
    var nextBtn = modal.querySelector('[data-wizard-next]');
    var detailToggle = modal.querySelector('[data-wizard-detail-toggle]');
    var closeButtons = modal.querySelectorAll('[data-wizard-close]');

    var state = loadState() || defaultState();
    var triggerEl = null;       // element that opened the modal — focus returns here on close
    var wasResumed = !!loadState();

    if (counterTotal) counterTotal.textContent = String(STEPS.length);

    /* ── Modal lifecycle ──────────────────────────────── */

    function openModal(opts) {
      opts = opts || {};
      triggerEl = opts.trigger || document.activeElement;
      modal.removeAttribute('hidden');
      // Mark the rest of the page inert so keyboard / AT can't reach
      // the underlying content.
      var pageRoot = document.querySelector('nav.navbar') ? document.body : null;
      if (pageRoot) {
        Array.prototype.forEach.call(pageRoot.children, function (child) {
          if (child !== modal && !child.contains(modal)) {
            child.setAttribute('aria-hidden', 'true');
          }
        });
      }
      document.body.style.overflow = 'hidden';
      render();
      // Push ?wizard=open onto the URL without reloading so a refresh /
      // share-link round-trips back into the wizard at the same step.
      try {
        var params = new URLSearchParams(window.location.search);
        params.set('wizard', 'open');
        window.history.replaceState({ wizard: 'open' }, '', window.location.pathname + '?' + params.toString());
      } catch (e) { /* noop */ }
      setTimeout(function () {
        if (dialog) dialog.focus();
        var firstFocusable = pane && pane.querySelector('button, input, select, textarea, [tabindex="0"]');
        if (firstFocusable) firstFocusable.focus();
      }, 50);
    }

    function closeModal() {
      modal.setAttribute('hidden', '');
      document.body.style.overflow = '';
      Array.prototype.forEach.call(document.body.children, function (child) {
        if (child !== modal) child.removeAttribute('aria-hidden');
      });
      try {
        var params = new URLSearchParams(window.location.search);
        params.delete('wizard');
        var qs = params.toString();
        window.history.replaceState({}, '', window.location.pathname + (qs ? '?' + qs : ''));
      } catch (e) { /* noop */ }
      if (triggerEl && typeof triggerEl.focus === 'function') {
        try { triggerEl.focus(); } catch (e) { /* noop */ }
      }
    }

    /* ── Focus trap (Tab / Shift+Tab cycle inside dialog) */

    dialog.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        closeModal();
        e.preventDefault();
        return;
      }
      if (e.key !== 'Tab') return;
      var focusables = dialog.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (focusables.length === 0) return;
      var first = focusables[0];
      var last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        last.focus();
        e.preventDefault();
      } else if (!e.shiftKey && document.activeElement === last) {
        first.focus();
        e.preventDefault();
      }
    });

    /* ── State + render ───────────────────────────────── */

    function persist() { saveState(state); }

    function render() {
      var idx = state.step;
      if (idx < 0) idx = 0;
      if (idx >= STEPS.length) idx = STEPS.length - 1;
      state.step = idx;

      coerceForRisk(state);

      // Rebuilding the pane drops the focused control, so a keyboard user who
      // toggled a field that reveals others would restart at the top of the
      // dialog. Controls have stable ids (controlId): focus the rebuilt one.
      // After a step change the id is gone and focus is left alone.
      var active = document.activeElement;
      var focusedId = active && active.id && pane.contains(active) ? active.id : null;
      // A sibling tab's update re-renders under the caret: keep its position too.
      var caret = focusedId && typeof active.selectionStart === 'number'
        ? [active.selectionStart, active.selectionEnd] : null;

      var stepDef = STEPS[idx];
      pane.innerHTML = '';
      var renderer = STEP_RENDERERS[stepDef.id];
      if (renderer) renderer(pane, state, render, persist);
      var refocus = focusedId ? document.getElementById(focusedId) : null;
      if (refocus && pane.contains(refocus)) {
        refocus.focus();
        if (caret && typeof refocus.setSelectionRange === 'function') {
          try { refocus.setSelectionRange(caret[0], caret[1]); } catch (e) { /* number inputs have no caret */ }
        }
      }

      // Progress + counter
      var pct = Math.round(((idx + 1) / STEPS.length) * 100);
      if (progress) progress.style.width = pct + '%';
      if (counterCurrent) counterCurrent.textContent = String(idx + 1);
      if (stepName) stepName.textContent = tr(stepDef.nameKey);

      // Prev / Next
      prevBtn.disabled = idx === 0;
      var isLast = idx === STEPS.length - 1;
      var nextLabel = nextBtn.querySelector('span');
      if (nextLabel) {
        nextLabel.textContent = isLast ? tr('wizard.nav.done') : tr('wizard.nav.next');
      }

      // Detail toggle visibility — only steps that ship a tutorial
      // paragraph need the toggle.
      if (detailToggle) {
        var showsTutorial = stepDef.id !== 'welcome' && stepDef.id !== 'review';
        if (showsTutorial) {
          detailToggle.removeAttribute('hidden');
          detailToggle.classList.toggle('is-active', state.detailsVisible);
          var detailLabel = detailToggle.querySelector('span');
          if (detailLabel) {
            detailLabel.textContent = state.detailsVisible
              ? tr('wizard.detail.hide')
              : tr('wizard.detail.show');
          }
        } else {
          detailToggle.setAttribute('hidden', '');
        }
      }

      // YAML preview
      var yamlText = buildYaml(state);
      if (yamlOutput) yamlOutput.innerHTML = tintYaml(yamlText);

      persist();
    }

    // Map an invalid field name back to the accordion that hosts it.
    // When the operator hits Next on an invalid step, we auto-open the
    // accordion containing the first error so they don't have to hunt
    // for it — particularly important for collapsed sections like
    // governance / risk_assessment / eval_gates / webhook / monitoring.
    var FIELD_TO_ACCORDION = {
      collectionMethod:       'governance',
      annotationProcess:      'governance',
      knownBiases:            'governance',
      intendedUse:            'risk_assessment',
      foreseeableMisuse:      'risk_assessment',
      mitigationMeasures:     'risk_assessment',
      benchmarkTasks:         'eval_gates',
      judgeApiKeyEnv:         'eval_gates',
      webhookUrl:             'webhook',
      monitoringEndpoint:     'monitoring',
      monitoringCheckIntervalHours: 'monitoring',
      syntheticTeacherModel:  'synthetic',
      syntheticSeedFile:      'synthetic',
      syntheticApiKeyEnv:     'synthetic'
    };

    function goNext() {
      // Validate the CURRENT step before advancing. On failure, mark
      // the step as "attempted" so the renderer shows inline errors
      // next to the offending fields, and rerender in place — the
      // operator stays on the same step until they fix the input.
      var currentStepId = STEPS[state.step].id;
      var errors = validateStep(state, currentStepId);
      if (errors.length > 0) {
        if (!state.attemptedSteps) state.attemptedSteps = [];
        if (state.attemptedSteps.indexOf(currentStepId) < 0) {
          state.attemptedSteps.push(currentStepId);
        }
        // Auto-open every accordion that hosts an invalid field, so
        // the offending input is actually visible after rerender.
        if (!state.openAccordions) state.openAccordions = {};
        for (var i = 0; i < errors.length; i++) {
          var accId = FIELD_TO_ACCORDION[errors[i].field];
          if (accId) state.openAccordions[accId] = true;
        }
        persist();
        render();
        // Move the keyboard focus + viewport to the FIRST invalid input.
        // We focus the input itself rather than the .wizard-error sibling
        // so screen readers announce the field label and the operator
        // can start typing immediately. ``preventScroll: true`` on focus
        // lets us drive the scroll separately with smooth behaviour.
        var firstInvalid = pane.querySelector('.wizard-input.is-invalid');
        if (firstInvalid) {
          if (typeof firstInvalid.focus === 'function') {
            try { firstInvalid.focus({ preventScroll: true }); }
            catch (_) { firstInvalid.focus(); }
          }
          if (typeof firstInvalid.scrollIntoView === 'function') {
            firstInvalid.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        }
        return;
      }
      if (state.step < STEPS.length - 1) {
        state.step += 1;
        render();
        pane.scrollTop = 0;
      } else {
        // Final step: "Done" closes the modal but keeps state.
        closeModal();
      }
    }
    function goPrev() {
      if (state.step > 0) {
        state.step -= 1;
        render();
        pane.scrollTop = 0;
      }
    }

    /* ── Triggers + buttons ───────────────────────────── */

    document.querySelectorAll('[data-wizard-open]').forEach(function (btn) {
      btn.addEventListener('click', function () { openModal({ trigger: btn }); });
    });
    closeButtons.forEach(function (btn) {
      btn.addEventListener('click', closeModal);
    });
    prevBtn.addEventListener('click', goPrev);
    nextBtn.addEventListener('click', goNext);

    if (detailToggle) {
      detailToggle.addEventListener('click', function () {
        state.detailsVisible = !state.detailsVisible;
        render();
      });
    }

    /* ── YAML pane: copy + mobile accordion toggle ────── */

    if (yamlCopy && yamlOutput) {
      yamlCopy.addEventListener('click', function () {
        var text = yamlOutput.innerText || yamlOutput.textContent || '';
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            yamlCopy.classList.add('is-copied');
            var label = yamlCopy.querySelector('span');
            if (label) {
              var prev = label.textContent;
              label.textContent = tr('common.copy.done');
              setTimeout(function () {
                yamlCopy.classList.remove('is-copied');
                if (label) label.textContent = prev;
              }, 1600);
            }
          });
        }
      });
    }
    if (yamlToggle && yamlPane) {
      yamlToggle.addEventListener('click', function () {
        var expanded = yamlPane.classList.toggle('is-expanded');
        yamlToggle.setAttribute('aria-expanded', String(expanded));
      });
    }

    /* ── URL parameter: ?wizard=open auto-opens ───────── */

    try {
      var params = new URLSearchParams(window.location.search);
      if (params.get('wizard') === 'open') {
        // Defer so the rest of the page (lang dropdown, theme,
        // translations) finishes initialising before the dialog
        // pulls focus.
        setTimeout(function () { openModal({ trigger: null }); }, 200);
      }
    } catch (e) { /* noop */ }

    // Close on browser back when the modal is open.
    window.addEventListener('popstate', function () {
      if (!modal.hasAttribute('hidden')) closeModal();
    });

    // C2 (review-cycle 2): cross-tab synchronisation.  The wizard
    // persists state to a single ``localStorage`` key; without a
    // listener, two tabs editing the same wizard race — last write
    // wins, mid-edit answers in tab A vanish on render in tab B.
    // ``storage`` events fire in OTHER tabs (not the writer), so we
    // can detect when a sibling tab updated the wizard and either
    // reload its state into our own UI or warn the operator.  The
    // simpler "reload state" branch is correct for the common case
    // (operator is comparing two configs) and recoverable for the
    // edge case (operator is mid-edit in BOTH tabs simultaneously).
    window.addEventListener('storage', function (e) {
      if (e.key !== STORAGE_KEY) return;
      if (modal.hasAttribute('hidden')) return; // not visible — nothing to refresh
      try {
        // Cross-tab reset: a sibling tab cleared the wizard
        // (operator typed `reset`/`r` or finished successfully); the
        // ``storage`` event arrives with ``e.newValue === null``.  We
        // can't ``loadState`` ourselves because nothing is persisted,
        // so explicitly reset the in-memory state to defaults and
        // re-render so this tab's UI doesn't drift into a config that
        // no longer exists.
        if (e.newValue === null) {
          var fallback = defaultState();
          Object.keys(state).forEach(function (k) { delete state[k]; });
          Object.keys(fallback).forEach(function (k) { state[k] = fallback[k]; });
          render();
          return;
        }
        var fresh = loadState();
        if (fresh) {
          // Mutate state in place so closures (persist, render) keep
          // their reference.  Re-render with the new values.
          Object.keys(state).forEach(function (k) { delete state[k]; });
          Object.keys(fresh).forEach(function (k) { state[k] = fresh[k]; });
          render();
        }
      } catch (err) {
        // B11 (review-cycle 3): the previous version silently swallowed
        // every error here, leaving the operator with no clue why a
        // sibling tab's edits failed to propagate.  ``loadState`` already
        // logs YAML / version-mismatch issues to the console; surface
        // anything ELSE (e.g. structuredClone-style errors, quota loss)
        // explicitly so debugging is possible.  Soft visibility — no
        // user-facing modal, just devtools breadcrumbs.
        if (window.console && window.console.warn) {
          window.console.warn('[forgelm-wizard] cross-tab state sync failed:', err);
        }
      }
    });

    // Surface a resume banner in the welcome step the first time the
    // operator reopens with a saved session.
    if (wasResumed) {
      modal.dataset.resumed = 'true';
    }
  }

  /* ── Step renderers ───────────────────────────────────── */

  // Each renderer takes the pane element + the state object + a
  // re-render callback + a persist callback. They build the step
  // content via DOM construction rather than templating so we don't
  // need a templating library and the resulting nodes own typed
  // event handlers (no innerHTML interpolation of state values).
  var STEP_RENDERERS = {};

  // Collapsible accordion section for grouping advanced / optional
  // fields inside an existing step. Title is a button that toggles
  // the open/closed state (persisted on the state object so closing +
  // re-opening the wizard keeps the operator's preference). The body
  // builder runs every render so the children stay current with state.
  // SVG icon set used by wizard accordions. Each accordionId maps to
  // a single Lucide-style line icon — the icon visually anchors the
  // section's purpose so the operator can scan a stack of accordions
  // and find the one they want without reading every label. Using
  // ``currentColor`` lets the CSS theme-aware open/closed state flip
  // tint without re-rendering the SVG.
  var ACCORDION_ICONS = {
    governance:      '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>',
    risk_assessment: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    eval_gates:      '<circle cx="12" cy="12" r="10"/><polyline points="9 12 11 14 15 10"/>',
    webhook:         '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    monitoring:      '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    synthetic:       '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>',
    retention:       '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>'
  };

  // Render an inline SVG with the icon paths for the given accordionId.
  // Returns null when the id has no registered icon — the caller then
  // omits the icon slot entirely so older accordions don't grow a
  // broken-icon hole.
  function accordionIconNode(accordionId) {
    var paths = ACCORDION_ICONS[accordionId];
    if (!paths) return null;
    return el('span', {
      class: 'wizard-accordion-icon',
      'aria-hidden': 'true',
      html: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + paths + '</svg>'
    });
  }

  // Chevron rendered as SVG instead of the previous unicode arrow.
  // Rotation is driven by the parent ``.is-open`` class via CSS, not
  // an inline style attribute, so the transition keyframes match the
  // body-expand animation timing.
  function accordionChevronNode() {
    return el('span', {
      class: 'wizard-accordion-chevron',
      'aria-hidden': 'true',
      html: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>'
    });
  }

  function makeAccordion(state, persist, accordionId, titleKey, hintKey, buildBody) {
    if (!state.openAccordions) state.openAccordions = {};
    var isOpen = !!state.openAccordions[accordionId];
    var section = el('section', { class: 'wizard-accordion' + (isOpen ? ' is-open' : '') });

    var header = el('button', {
      type: 'button',
      class: 'wizard-accordion-header',
      'aria-expanded': isOpen ? 'true' : 'false',
      on: {
        click: function () {
          state.openAccordions[accordionId] = !isOpen;
          persist();
          // Toggle in place; child build is already attached. Just
          // flip the open/expanded class + aria attribute. CSS drives
          // the chevron rotation + body grid-rows animation off the
          // ``.is-open`` class — no inline style mutation needed.
          var newOpen = !!state.openAccordions[accordionId];
          isOpen = newOpen;
          section.classList.toggle('is-open', newOpen);
          header.setAttribute('aria-expanded', newOpen ? 'true' : 'false');
        }
      }
    });

    var iconNode = accordionIconNode(accordionId);
    if (iconNode) header.appendChild(iconNode);

    // Title + hint stack vertically in a single column so the row
    // layout (icon | titles | chevron) stays consistent across short
    // and long hints.
    var titlesCol = el('div', { class: 'wizard-accordion-titles' });
    titlesCol.appendChild(el('span', { class: 'wizard-accordion-title', text: tr(titleKey) }));
    if (hintKey) {
      titlesCol.appendChild(el('span', { class: 'wizard-accordion-hint', text: tr(hintKey) }));
    }
    header.appendChild(titlesCol);
    header.appendChild(accordionChevronNode());
    section.appendChild(header);

    // Body wrapper enables the smooth grid-rows expand/collapse
    // animation. ``.wizard-accordion-body-wrap`` is a 0fr/1fr grid
    // controlled by CSS; ``.wizard-accordion-body`` is its only child
    // and stays fully rendered so the buildBody callback's listeners
    // survive open/close cycles.
    var bodyWrap = el('div', { class: 'wizard-accordion-body-wrap' });
    var body = el('div', { class: 'wizard-accordion-body' });
    buildBody(body);
    bodyWrap.appendChild(body);
    section.appendChild(bodyWrap);
    return section;
  }

  function stepHeader(state, titleKey, descKey, tutorialKey) {
    var nodes = [];
    // ``id="wizard-title"`` matches the dialog's aria-labelledby on
    // quickstart.html — without it the WAI-ARIA reference is an
    // orphan and screen readers announce "dialog" with no name.
    nodes.push(el('h2', { class: 'wizard-step-title', id: 'wizard-title', text: tr(titleKey) }));
    if (descKey) nodes.push(el('p', { class: 'wizard-step-desc', text: tr(descKey) }));
    if (tutorialKey && state.detailsVisible) {
      nodes.push(el('div', { class: 'wizard-step-tutorial', text: tr(tutorialKey) }));
    }
    return nodes;
  }

  // Append a per-field detail paragraph beneath a row while the operator
  // has details enabled (state.detailsVisible). The paragraph expands what
  // the hint glosses: 2-3 sentences on what the value MEANS, not just what
  // range to pick. Shared by every renderer that shows per-field details —
  // it used to be a local helper in three of them, and the compliance
  // renderer, which had none, threw a ReferenceError before its risk and
  // evaluation controls (#879).
  function appendDetail(state, rowEl, detailKey) {
    if (state.detailsVisible && detailKey) {
      rowEl.appendChild(el('p', { class: 'wizard-row-detail', text: tr(detailKey) }));
    }
  }

  // Id of the control that edits ``field``; a caption points at it with
  // ``for`` so the caption is the control's accessible name and a click on
  // it focuses the control (#950). Checkboxes keep their wrapping <label>;
  // their ids, and ``controlId(field) + '-' + option`` on option cards, let
  // render() return focus to the control that re-rendered the step. Field
  // names make the ids stable and unique: each control writes one state
  // field and a step renders each field at most once, so a re-render
  // rebuilds the same ids rather than adding second copies.
  function controlId(field) {
    return 'wizard-field-' + field.replace(/[A-Z]/g, function (c) { return '-' + c.toLowerCase(); });
  }

  /* Step 1: Welcome / experience level */
  STEP_RENDERERS['welcome'] = function (pane, state, rerender, persist) {
    pane.appendChild(el('h2', { class: 'wizard-step-title', id: 'wizard-title', text: tr('wizard.step.welcome.title') }));
    pane.appendChild(el('p', { class: 'wizard-step-desc', text: tr('wizard.step.welcome.desc') }));
    pane.appendChild(el('div', { class: 'wizard-step-tutorial', text: tr('wizard.step.welcome.tutorial') }));

    var cards = el('div', { class: 'wizard-cards' });
    [
      { id: 'beginner', titleKey: 'wizard.exp.beginner.title', descKey: 'wizard.exp.beginner.desc' },
      { id: 'expert',   titleKey: 'wizard.exp.expert.title',   descKey: 'wizard.exp.expert.desc' }
    ].forEach(function (opt) {
      var card = el('button', {
        type: 'button',
        id: controlId('experience') + '-' + opt.id,
        class: 'wizard-card' + (state.experience === opt.id ? ' is-selected' : ''),
        on: {
          click: function () {
            state.experience = opt.id;
            state.detailsVisible = (opt.id === 'beginner');
            persist();
            rerender();
          }
        }
      }, [
        el('span', { class: 'wizard-card-title', text: tr(opt.titleKey) }),
        el('span', { class: 'wizard-card-desc',  text: tr(opt.descKey) })
      ]);
      cards.appendChild(card);
    });
    pane.appendChild(cards);
  };

  /* Step 2: Use case */
  STEP_RENDERERS['use-case'] = function (pane, state, rerender, persist) {
    stepHeader(state, 'wizard.step.usecase.title', 'wizard.step.usecase.desc', 'wizard.step.usecase.tutorial')
      .forEach(function (n) { pane.appendChild(n); });

    var grid = el('div', { class: 'wizard-cards' });
    [
      { id: 'customer-support', titleKey: 'wizard.usecase.support.title', badgeKey: 'wizard.usecase.support.badge', descKey: 'wizard.usecase.support.desc' },
      { id: 'code-assistant',     titleKey: 'wizard.usecase.code.title',    badgeKey: 'wizard.usecase.code.badge',    descKey: 'wizard.usecase.code.desc' },
      { id: 'domain-expert',    titleKey: 'wizard.usecase.domain.title',  badgeKey: 'wizard.usecase.domain.badge',  descKey: 'wizard.usecase.domain.desc' },
      { id: 'grpo-math',        titleKey: 'wizard.usecase.math.title',    badgeKey: 'wizard.usecase.math.badge',    descKey: 'wizard.usecase.math.desc' },
      { id: 'medical-qa-tr',       titleKey: 'wizard.usecase.medical.title', badgeKey: 'wizard.usecase.medical.badge', descKey: 'wizard.usecase.medical.desc' },
      { id: 'custom',           titleKey: 'wizard.usecase.custom.title',  badgeKey: '',                              descKey: 'wizard.usecase.custom.desc' }
    ].forEach(function (opt) {
      var titleNode = el('span', { class: 'wizard-card-title' }, [tr(opt.titleKey)]);
      if (opt.badgeKey) {
        titleNode.appendChild(el('span', { class: 'badge', text: tr(opt.badgeKey) }));
      }
      var card = el('button', {
        type: 'button',
        id: controlId('useCase') + '-' + opt.id,
        class: 'wizard-card' + (state.useCase === opt.id ? ' is-selected' : ''),
        on: {
          click: function () {
            applyUseCasePreset(state, opt.id);
            persist();
            rerender();
          }
        }
      }, [
        titleNode,
        el('span', { class: 'wizard-card-desc', text: tr(opt.descKey) })
      ]);
      grid.appendChild(card);
    });
    pane.appendChild(grid);
  };

  /* Step 3: Trainer (alignment paradigm). Surfaces all 6 schema-supported
     trainer_type values as cards. The use-case step preselects a sensible
     default but the operator can override here. Each card shows its data
     shape requirement so the operator knows what JSONL rows the trainer
     will accept (SFT: messages/prompt+response, preference trainers:
     chosen+rejected, KTO: binary feedback, GRPO: prompt+gold_answer). */
  STEP_RENDERERS['trainer'] = function (pane, state, rerender, persist) {
    stepHeader(state, 'wizard.step.trainer.title', 'wizard.step.trainer.desc', 'wizard.step.trainer.tutorial')
      .forEach(function (n) { pane.appendChild(n); });

    var grid = el('div', { class: 'wizard-cards' });
    TRAINERS.forEach(function (def) {
      var titleNode = el('span', { class: 'wizard-card-title' }, [
        tr('wizard.trainer.' + def.id + '.title')
      ]);
      titleNode.appendChild(el('span', {
        class: 'badge',
        text: tr('wizard.trainer.' + def.id + '.badge')
      }));
      if (def.recommended) {
        titleNode.appendChild(el('span', {
          class: 'badge',
          text: tr('wizard.trainer.recommended'),
          style: 'background: rgba(249,115,22,0.15); color: var(--ember-bright); border-color: var(--ember);'
        }));
      }
      var card = el('button', {
        type: 'button',
        id: controlId('trainerType') + '-' + def.id,
        class: 'wizard-card' + (state.trainerType === def.id ? ' is-selected' : ''),
        on: {
          click: function () {
            state.trainerType = def.id;
            persist();
            rerender();
          }
        }
      }, [
        titleNode,
        el('span', { class: 'wizard-card-desc', text: tr('wizard.trainer.' + def.id + '.desc') }),
        el('span', {
          class: 'wizard-card-desc',
          style: 'font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-muted); margin-top: 0.4rem;',
          text: tr('wizard.trainer.' + def.id + '.shape')
        })
      ]);
      grid.appendChild(card);
    });
    pane.appendChild(grid);
  };

  /* Step 4: Base model */
  STEP_RENDERERS['model'] = function (pane, state, rerender, persist) {
    stepHeader(state, 'wizard.step.model.title', 'wizard.step.model.desc', 'wizard.step.model.tutorial')
      .forEach(function (n) { pane.appendChild(n); });

    var presets = [
      { id: 'llama3-8b',   path: 'meta-llama/Llama-3.1-8B-Instruct',     titleKey: 'wizard.model.llama3.title',   badgeKey: 'wizard.model.llama3.badge',   descKey: 'wizard.model.llama3.desc' },
      { id: 'qwen-7b',     path: 'Qwen/Qwen2.5-7B-Instruct',              titleKey: 'wizard.model.qwen.title',     badgeKey: '',                            descKey: 'wizard.model.qwen.desc' },
      { id: 'mistral-7b',  path: 'mistralai/Mistral-7B-Instruct-v0.3',   titleKey: 'wizard.model.mistral.title',  badgeKey: '',                            descKey: 'wizard.model.mistral.desc' },
      { id: 'phi3-mini',   path: 'microsoft/Phi-3-mini-4k-instruct',      titleKey: 'wizard.model.phi.title',      badgeKey: 'wizard.model.phi.badge',      descKey: 'wizard.model.phi.desc' },
      { id: 'custom',      path: '',                                      titleKey: 'wizard.model.custom.title',   badgeKey: '',                            descKey: 'wizard.model.custom.desc' }
    ];

    var grid = el('div', { class: 'wizard-cards' });
    presets.forEach(function (preset) {
      var titleNode = el('span', { class: 'wizard-card-title' }, [tr(preset.titleKey)]);
      if (preset.badgeKey) titleNode.appendChild(el('span', { class: 'badge', text: tr(preset.badgeKey) }));
      var card = el('button', {
        type: 'button',
        id: controlId('modelPreset') + '-' + preset.id,
        class: 'wizard-card' + (state.modelPreset === preset.id ? ' is-selected' : ''),
        on: {
          click: function () {
            state.modelPreset = preset.id;
            if (preset.id !== 'custom') state.model = preset.path;
            persist();
            rerender();
          }
        }
      }, [
        titleNode,
        el('span', { class: 'wizard-card-desc', text: tr(preset.descKey) })
      ]);
      grid.appendChild(card);
    });
    pane.appendChild(grid);

    if (state.modelPreset === 'custom') {
      var modelErrKey = fieldHasError(state, 'model', 'model');
      var modelError = modelErrKey ? el('div', { class: 'wizard-error', role: 'alert', text: tr(modelErrKey) }) : null;
      var input = el('input', {
        id: controlId('model'),
        type: 'text',
        class: 'wizard-input' + (modelErrKey ? ' is-invalid' : ''),
        placeholder: 'huggingface-org/your-model',
        value: state.model || '',
        'aria-invalid': modelErrKey ? 'true' : 'false'
      });
      input.addEventListener('input', function () {
        state.model = input.value.trim();
        persist();
        // A field Next flagged drops its error, in place, once the new value
        // validates; a re-render would rebuild and re-announce the step.
        if (modelError && !validateStep(state, 'model').some(function (e) { return e.field === 'model'; })) {
          input.classList.remove('is-invalid');
          input.setAttribute('aria-invalid', 'false');
          modelError.remove();
          modelError = null;
        }
        // Refresh the YAML preview in place — keeps focus + caret
        // position on the input.
        var yamlOutput = document.querySelector('[data-wizard-yaml-output]');
        if (yamlOutput) yamlOutput.innerHTML = tintYaml(buildYaml(state));
      });
      var rowChildren = [
        el('label', { for: controlId('model'), class: 'wizard-row-label', text: tr('wizard.model.custom.label') }),
        input,
        el('span', { class: 'wizard-row-hint', text: tr('wizard.model.custom.hint') })
      ];
      if (modelError) rowChildren.push(modelError);
      pane.appendChild(el('div', { class: 'wizard-row' }, rowChildren));
    }
  };

  /* Step 4: Dataset */
  STEP_RENDERERS['dataset'] = function (pane, state, rerender, persist) {
    stepHeader(state, 'wizard.step.dataset.title', 'wizard.step.dataset.desc', 'wizard.step.dataset.tutorial')
      .forEach(function (n) { pane.appendChild(n); });

    var grid = el('div', { class: 'wizard-cards' });
    [
      { id: 'huggingface', titleKey: 'wizard.dataset.hf.title',    descKey: 'wizard.dataset.hf.desc' },
      { id: 'local-jsonl', titleKey: 'wizard.dataset.jsonl.title', descKey: 'wizard.dataset.jsonl.desc' },
      { id: 'local-pdf',   titleKey: 'wizard.dataset.pdf.title',   descKey: 'wizard.dataset.pdf.desc' }
    ].forEach(function (opt) {
      var card = el('button', {
        type: 'button',
        id: controlId('datasetKind') + '-' + opt.id,
        class: 'wizard-card' + (state.datasetKind === opt.id ? ' is-selected' : ''),
        on: {
          click: function () {
            state.datasetKind = opt.id;
            persist();
            rerender();
          }
        }
      }, [
        el('span', { class: 'wizard-card-title', text: tr(opt.titleKey) }),
        el('span', { class: 'wizard-card-desc',  text: tr(opt.descKey) })
      ]);
      grid.appendChild(card);
    });
    pane.appendChild(grid);

    var datasetErrKey = fieldHasError(state, 'dataset', 'datasetName');
    var datasetError = datasetErrKey ? el('div', { class: 'wizard-error', role: 'alert', text: tr(datasetErrKey) }) : null;
    var nameInput = el('input', {
      id: controlId('datasetName'),
      type: 'text',
      class: 'wizard-input' + (datasetErrKey ? ' is-invalid' : ''),
      placeholder: state.datasetKind === 'huggingface'
        ? 'org/dataset-name'
        : (state.datasetKind === 'local-pdf' ? './documents/' : './data/train.jsonl'),
      value: state.datasetName,
      'aria-invalid': datasetErrKey ? 'true' : 'false'
    });
    nameInput.addEventListener('input', function () {
      state.datasetName = nameInput.value.trim();
      persist();
      if (datasetError && !validateStep(state, 'dataset').some(function (e) { return e.field === 'datasetName'; })) {
        nameInput.classList.remove('is-invalid');
        nameInput.setAttribute('aria-invalid', 'false');
        datasetError.remove();
        datasetError = null;
      }
      var yamlOutput = document.querySelector('[data-wizard-yaml-output]');
      if (yamlOutput) yamlOutput.innerHTML = tintYaml(buildYaml(state));
    });
    var dsRowChildren = [
      el('label', { for: controlId('datasetName'), class: 'wizard-row-label', text: tr('wizard.dataset.name.label') }),
      nameInput,
      el('span', { class: 'wizard-row-hint', text: tr('wizard.dataset.name.hint') })
    ];
    if (datasetError) dsRowChildren.push(datasetError);
    pane.appendChild(el('div', { class: 'wizard-row' }, dsRowChildren));

    // Trainer-aware shape guidance. SFT auto-detects messages or
    // prompt+response, but preference / KTO / GRPO trainers expect
    // specific row shapes that DataConfig does NOT auto-detect — the
    // operator must format the JSONL accordingly. We surface that
    // expectation here instead of leaving them to find it in the
    // runtime error.
    var dsTrDef = trainerDef(state.trainerType);
    var shapeKey;
    if (dsTrDef.isPreference) shapeKey = 'preference';
    else if (dsTrDef.isKto)    shapeKey = 'kto';
    else if (dsTrDef.isGrpo)   shapeKey = 'grpo';
    else                        shapeKey = 'sft';
    pane.appendChild(el('div', {
      class: 'wizard-step-tutorial',
      html: '<strong>' + tr('wizard.dataset.shape.' + shapeKey + '.title') + '</strong> ' + tr('wizard.dataset.shape.' + shapeKey + '.body')
    }));

    function liveYaml() {
      var yamlOutput = document.querySelector('[data-wizard-yaml-output]');
      if (yamlOutput) yamlOutput.innerHTML = tintYaml(buildYaml(state));
    }

    // Data Governance accordion (EU AI Act Article 10).
    pane.appendChild(makeAccordion(state, persist, 'governance',
      'wizard.governance.title', 'wizard.governance.hint', function (body) {

      // High-risk + unacceptable tiers carry mandatory Article 10
      // documentation. We pass the error i18n key in only when those
      // tiers are active; everywhere else the textarea stays optional.
      var requireGov = isStrictRisk(state);
      function makeTextarea(field, labelKey, hintKey, placeholderKey, detailKey, rows, errorKey) {
        var errKey = errorKey ? fieldHasError(state, 'dataset', field) : null;
        var ta = el('textarea', {
          id: controlId(field),
          class: 'wizard-input' + (errKey ? ' is-invalid' : ''),
          rows: String(rows || 2),
          placeholder: tr(placeholderKey)
        });
        ta.value = state[field] || '';
        var rowChildren = [
          el('label', { for: controlId(field), class: 'wizard-row-label', text: tr(labelKey) }),
          ta,
          el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
        ];
        if (errKey) {
          rowChildren.push(el('div', { class: 'wizard-error', role: 'alert', text: tr(errKey) }));
        }
        var row = el('div', { class: 'wizard-row' }, rowChildren);
        ta.addEventListener('input', function () {
          state[field] = ta.value;
          persist();
          liveYaml();
          if (errorKey) {
            var stillErr = validateStep(state, 'dataset').some(function (e) { return e.field === field; });
            if (!stillErr) {
              ta.classList.remove('is-invalid');
              var errEl = row.querySelector('.wizard-error');
              if (errEl) errEl.remove();
            }
          }
        });
        appendDetail(state, row, detailKey);
        return row;
      }

      body.appendChild(makeTextarea('collectionMethod',
        'wizard.governance.collection_method.label',
        'wizard.governance.collection_method.hint',
        'wizard.governance.collection_method.placeholder',
        'wizard.governance.collection_method.detail', 2,
        requireGov ? 'wizard.error.governance.collection_method.required' : null));
      body.appendChild(makeTextarea('annotationProcess',
        'wizard.governance.annotation_process.label',
        'wizard.governance.annotation_process.hint',
        'wizard.governance.annotation_process.placeholder',
        'wizard.governance.annotation_process.detail', 2,
        requireGov ? 'wizard.error.governance.annotation_process.required' : null));
      body.appendChild(makeTextarea('knownBiases',
        'wizard.governance.known_biases.label',
        'wizard.governance.known_biases.hint',
        'wizard.governance.known_biases.placeholder',
        'wizard.governance.known_biases.detail', 2,
        requireGov ? 'wizard.error.governance.known_biases.required' : null));

      function makeCheckbox(field, labelKey, hintKey) {
        var cb = el('input', { id: controlId(field), type: 'checkbox' });
        cb.checked = !!state[field];
        cb.addEventListener('change', function () {
          state[field] = cb.checked;
          persist();
          liveYaml();
        });
        return el('div', { class: 'wizard-row' }, [
          el('label', { class: 'wizard-toggle' }, [
            cb,
            el('span', { class: 'wizard-toggle-label', text: tr(labelKey) })
          ]),
          el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
        ]);
      }
      body.appendChild(makeCheckbox('personalDataIncluded',
        'wizard.governance.personal_data.label',
        'wizard.governance.personal_data.hint'));
      body.appendChild(makeCheckbox('dpiaCompleted',
        'wizard.governance.dpia_completed.label',
        'wizard.governance.dpia_completed.hint'));
    }));
  };

  /* Step 5: Training params */
  STEP_RENDERERS['training'] = function (pane, state, rerender, persist) {
    stepHeader(state, 'wizard.step.training.title', 'wizard.step.training.desc', 'wizard.step.training.tutorial')
      .forEach(function (n) { pane.appendChild(n); });

    function liveYaml() {
      var yamlOutput = document.querySelector('[data-wizard-yaml-output]');
      if (yamlOutput) yamlOutput.innerHTML = tintYaml(buildYaml(state));
    }

    // QLoRA toggle (4-bit base model quantization)
    var qloraInput = el('input', { id: controlId('qlora'), type: 'checkbox' });
    qloraInput.checked = state.qlora;
    qloraInput.addEventListener('change', function () {
      state.qlora = qloraInput.checked;
      persist();
      liveYaml();
    });
    var qloraRow = el('div', { class: 'wizard-row' }, [
      el('label', { class: 'wizard-toggle' }, [
        qloraInput,
        el('span', { class: 'wizard-toggle-label', text: tr('wizard.training.qlora.label') })
      ]),
      el('span', { class: 'wizard-row-hint', text: tr('wizard.training.qlora.hint') })
    ]);
    appendDetail(state, qloraRow, 'wizard.training.qlora.detail');
    pane.appendChild(qloraRow);

    // PEFT method radio (Standard LoRA / DoRA / PiSSA / RSLoRA).
    // Hidden when GaLore is enabled — GaLore replaces LoRA so the
    // method choice is irrelevant. Emits to lora.method in YAML.
    if (!state.galoreEnabled) {
      var methodRow = el('div', { class: 'wizard-row' });
      // One caption for four method buttons: a <label for> would replace a
      // button's own name, so the caption names their group instead.
      var methodCaptionId = controlId('loraMethod') + '-label';
      methodRow.appendChild(el('span', { id: methodCaptionId, class: 'wizard-row-label', text: tr('wizard.training.lora_method.label') }));
      var methods = [
        { id: 'lora',    titleKey: 'wizard.training.lora_method.standard.title',    descKey: 'wizard.training.lora_method.standard.desc' },
        { id: 'dora',    titleKey: 'wizard.training.lora_method.dora.title',        descKey: 'wizard.training.lora_method.dora.desc' },
        { id: 'pissa',   titleKey: 'wizard.training.lora_method.pissa.title',       descKey: 'wizard.training.lora_method.pissa.desc' },
        { id: 'rslora',  titleKey: 'wizard.training.lora_method.rslora.title',      descKey: 'wizard.training.lora_method.rslora.desc' }
      ];
      var methodGrid = el('div', {
        role: 'group',
        'aria-labelledby': methodCaptionId,
        class: 'wizard-cards',
        style: 'grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 0.6rem;'
      });
      methods.forEach(function (m) {
        var card = el('button', {
          type: 'button',
          id: controlId('loraMethod') + '-' + m.id,
          class: 'wizard-card' + (state.loraMethod === m.id ? ' is-selected' : ''),
          on: {
            click: function () {
              state.loraMethod = m.id;
              persist();
              liveYaml();
              // Re-render to update the selected-card visual state.
              rerender();
            }
          }
        }, [
          el('span', { class: 'wizard-card-title', text: tr(m.titleKey) }),
          el('span', { class: 'wizard-card-desc', text: tr(m.descKey) })
        ]);
        methodGrid.appendChild(card);
      });
      methodRow.appendChild(methodGrid);
      methodRow.appendChild(el('span', { class: 'wizard-row-hint', text: tr('wizard.training.lora_method.hint') }));
      appendDetail(state, methodRow, 'wizard.training.lora_method.detail');
      pane.appendChild(methodRow);
    }

    // GaLore toggle (full-parameter training via gradient projection).
    // Mutually exclusive with LoRA-style adapters at runtime.
    var galoreInput = el('input', { id: controlId('galoreEnabled'), type: 'checkbox' });
    galoreInput.checked = state.galoreEnabled;
    galoreInput.addEventListener('change', function () {
      state.galoreEnabled = galoreInput.checked;
      persist();
      // Re-render so the LoRA method radio + GaLore variant select
      // appear/disappear in line with the toggle.
      rerender();
    });
    var galoreRow = el('div', { class: 'wizard-row' }, [
      el('label', { class: 'wizard-toggle' }, [
        galoreInput,
        el('span', { class: 'wizard-toggle-label', text: tr('wizard.training.galore.label') })
      ]),
      el('span', { class: 'wizard-row-hint', text: tr('wizard.training.galore.hint') })
    ]);
    appendDetail(state, galoreRow, 'wizard.training.galore.detail');
    pane.appendChild(galoreRow);

    // GaLore variant select — only when GaLore is enabled.
    if (state.galoreEnabled) {
      var galoreSelect = el('select', { id: controlId('galoreOptim'), class: 'wizard-select' });
      [
        { id: 'galore_adamw',                 labelKey: 'wizard.training.galore_optim.adamw' },
        { id: 'galore_adamw_8bit',            labelKey: 'wizard.training.galore_optim.adamw_8bit' },
        { id: 'galore_adafactor',             labelKey: 'wizard.training.galore_optim.adafactor' },
        { id: 'galore_adamw_layerwise',       labelKey: 'wizard.training.galore_optim.adamw_layerwise' },
        { id: 'galore_adamw_8bit_layerwise',  labelKey: 'wizard.training.galore_optim.adamw_8bit_layerwise' },
        { id: 'galore_adafactor_layerwise',   labelKey: 'wizard.training.galore_optim.adafactor_layerwise' }
      ].forEach(function (opt) {
        var option = el('option', { value: opt.id, text: tr(opt.labelKey) });
        if (state.galoreOptim === opt.id) option.selected = true;
        galoreSelect.appendChild(option);
      });
      galoreSelect.addEventListener('change', function () {
        state.galoreOptim = galoreSelect.value;
        persist();
        liveYaml();
      });
      var galoreOptimRow = el('div', { class: 'wizard-row' }, [
        el('label', { for: controlId('galoreOptim'), class: 'wizard-row-label', text: tr('wizard.training.galore_optim.label') }),
        galoreSelect,
        el('span', { class: 'wizard-row-hint', text: tr('wizard.training.galore_optim.hint') })
      ]);
      appendDetail(state, galoreOptimRow, 'wizard.training.galore_optim.detail');
      pane.appendChild(galoreOptimRow);
    }

    // LoRA r / alpha sliders
    function makeSlider(field, min, max, hintKey, labelKey, detailKey) {
      var slider = el('input', {
        id: controlId(field),
        type: 'range',
        class: 'wizard-slider',
        min: String(min),
        max: String(max),
        value: String(state[field])
      });
      var valueOut = el('span', { class: 'wizard-slider-value', text: String(state[field]) });
      slider.addEventListener('input', function () {
        var v = parseInt(slider.value, 10);
        state[field] = v;
        valueOut.textContent = String(v);
        persist();
        liveYaml();
      });
      var row = el('div', { class: 'wizard-row' }, [
        el('label', { for: controlId(field), class: 'wizard-row-label', text: tr(labelKey) }),
        el('div', { class: 'wizard-slider-row' }, [slider, valueOut]),
        el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
      ]);
      appendDetail(state, row, detailKey);
      return row;
    }
    // LoRA rank / alpha — only meaningful when LoRA-style adapters
    // are in use. Hidden when GaLore is enabled because GaLore
    // bypasses LoRA entirely; the schema-required lora block still
    // emits with default r=8 / alpha=16 in the YAML.
    if (!state.galoreEnabled) {
      pane.appendChild(makeSlider('loraR',     4,  64, 'wizard.training.lora_r.hint',     'wizard.training.lora_r.label',     'wizard.training.lora_r.detail'));
      pane.appendChild(makeSlider('loraAlpha', 8, 128, 'wizard.training.lora_alpha.hint', 'wizard.training.lora_alpha.label', 'wizard.training.lora_alpha.detail'));
    }

    // Epochs
    var epochsInput = el('input', {
      id: controlId('epochs'),
      type: 'number',
      class: 'wizard-input',
      min: '1',
      max: '5',
      value: String(state.epochs)
    });
    epochsInput.addEventListener('input', function () {
      var v = parseInt(epochsInput.value, 10);
      if (!isNaN(v) && v >= 1 && v <= 5) {
        state.epochs = v;
        persist();
        liveYaml();
      }
    });
    var epochsRow = el('div', { class: 'wizard-row' }, [
      el('label', { for: controlId('epochs'), class: 'wizard-row-label', text: tr('wizard.training.epochs.label') }),
      epochsInput,
      el('span', { class: 'wizard-row-hint', text: tr('wizard.training.epochs.hint') })
    ]);
    appendDetail(state, epochsRow, 'wizard.training.epochs.detail');
    pane.appendChild(epochsRow);

    // Learning rate. Includes the schema default ``2e-5`` (review-
    // cycle 2 / C1+I3) so an operator who wants byte-for-byte parity
    // with ``ForgeConfig()`` can pick it from the dropdown rather than
    // hand-edit the YAML afterwards.
    var lrSelect = el('select', { id: controlId('learningRate'), class: 'wizard-select' });
    ['1e-4', '5e-5', '3e-5', '2e-5', '1e-5'].forEach(function (lr) {
      var option = el('option', { value: lr, text: lr });
      if (state.learningRate === lr) option.selected = true;
      lrSelect.appendChild(option);
    });
    lrSelect.addEventListener('change', function () {
      state.learningRate = lrSelect.value;
      persist();
      liveYaml();
    });
    var lrRow = el('div', { class: 'wizard-row' }, [
      el('label', { for: controlId('learningRate'), class: 'wizard-row-label', text: tr('wizard.training.lr.label') }),
      lrSelect,
      el('span', { class: 'wizard-row-hint', text: tr('wizard.training.lr.hint') })
    ]);
    appendDetail(state, lrRow, 'wizard.training.lr.detail');
    pane.appendChild(lrRow);

    // Batch size
    var batchSelect = el('select', { id: controlId('batchSize'), class: 'wizard-select' });
    [1, 2, 4, 8].forEach(function (bs) {
      var option = el('option', { value: String(bs), text: String(bs) });
      if (state.batchSize === bs) option.selected = true;
      batchSelect.appendChild(option);
    });
    batchSelect.addEventListener('change', function () {
      state.batchSize = parseInt(batchSelect.value, 10);
      persist();
      liveYaml();
    });
    var batchRow = el('div', { class: 'wizard-row' }, [
      el('label', { for: controlId('batchSize'), class: 'wizard-row-label', text: tr('wizard.training.batch.label') }),
      batchSelect,
      el('span', { class: 'wizard-row-hint', text: tr('wizard.training.batch.hint') })
    ]);
    appendDetail(state, batchRow, 'wizard.training.batch.detail');
    pane.appendChild(batchRow);

    // Max sequence length (P3 / review-cycle 2).  Was previously
    // omitted entirely — operators emitting an 8K-context model still
    // got ``model.max_length=2048`` (the schema default), causing
    // silent truncation halfway through long-form RAG / code-assist
    // prompts.  Surface as a number input clamped to the schema's
    // [64, 131072] range.
    var maxLengthInput = el('input', {
      id: controlId('maxLength'),
      type: 'number', class: 'wizard-input', value: String(state.maxLength || 2048),
      min: '64', max: '131072', step: '64'
    });
    maxLengthInput.addEventListener('change', function () {
      var parsed = parseInt(maxLengthInput.value, 10);
      if (!isNaN(parsed) && parsed >= 64 && parsed <= 131072) {
        state.maxLength = parsed;
        persist();
        liveYaml();
      } else {
        // Snap back to current persisted value on bogus input rather
        // than emit an invalid YAML.
        maxLengthInput.value = String(state.maxLength || 2048);
      }
    });
    var maxLengthRow = el('div', { class: 'wizard-row' }, [
      el('label', { for: controlId('maxLength'), class: 'wizard-row-label', text: tr('wizard.training.max_length.label') }),
      maxLengthInput,
      el('span', { class: 'wizard-row-hint', text: tr('wizard.training.max_length.hint') })
    ]);
    pane.appendChild(maxLengthRow);

    /* Trainer-specific hyperparameters. Conditionally rendered based
       on state.trainerType. The schema in forgelm/config.py defines
       these on TrainingConfig with sensible defaults; the wizard
       lets the operator override them and emits the chosen value to
       YAML so the run is reproducible. SFT has no trainer-specific
       params, so the section is hidden entirely for SFT runs. */
    var trDef2 = trainerDef(state.trainerType);
    if (trDef2.params.length > 0) {
      var sectionHeader = el('div', {
        class: 'wizard-step-tutorial',
        style: 'margin-top: 1.5rem;',
        html: '<strong>' + tr('wizard.training.trainer_section.title').replace('{trainer}', state.trainerType.toUpperCase()) + '</strong> ' + tr('wizard.training.trainer_section.body')
      });
      pane.appendChild(sectionHeader);

      // Helper for a numeric param row with a slider + live value
      function makeParamSlider(field, min, max, step, labelKey, hintKey, detailKey) {
        var slider = el('input', {
          id: controlId(field),
          type: 'range',
          class: 'wizard-slider',
          min: String(min),
          max: String(max),
          step: String(step),
          value: String(state[field])
        });
        var valueOut = el('span', {
          class: 'wizard-slider-value',
          text: String(state[field])
        });
        slider.addEventListener('input', function () {
          var v = parseFloat(slider.value);
          state[field] = v;
          valueOut.textContent = String(v);
          persist();
          liveYaml();
        });
        var row = el('div', { class: 'wizard-row' }, [
          el('label', { for: controlId(field), class: 'wizard-row-label', text: tr(labelKey) }),
          el('div', { class: 'wizard-slider-row' }, [slider, valueOut]),
          el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
        ]);
        appendDetail(state, row, detailKey);
        return row;
      }

      // Helper for an integer param input
      function makeParamNumber(field, min, max, step, labelKey, hintKey, detailKey) {
        var input = el('input', {
          id: controlId(field),
          type: 'number',
          class: 'wizard-input',
          min: String(min),
          max: String(max),
          step: String(step),
          value: String(state[field])
        });
        input.addEventListener('input', function () {
          var v = parseInt(input.value, 10);
          if (!isNaN(v) && v >= min && v <= max) {
            state[field] = v;
            persist();
            liveYaml();
          }
        });
        var row = el('div', { class: 'wizard-row' }, [
          el('label', { for: controlId(field), class: 'wizard-row-label', text: tr(labelKey) }),
          input,
          el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
        ]);
        appendDetail(state, row, detailKey);
        return row;
      }

      if (state.trainerType === 'dpo') {
        pane.appendChild(makeParamSlider('dpoBeta',     0.05, 0.5,  0.05,
          'wizard.training.dpo_beta.label',     'wizard.training.dpo_beta.hint',     'wizard.training.dpo_beta.detail'));
      } else if (state.trainerType === 'orpo') {
        pane.appendChild(makeParamSlider('orpoBeta',    0.05, 0.5,  0.05,
          'wizard.training.orpo_beta.label',    'wizard.training.orpo_beta.hint',    'wizard.training.orpo_beta.detail'));
      } else if (state.trainerType === 'simpo') {
        pane.appendChild(makeParamSlider('simpoGamma',  0.1,  2.0,  0.1,
          'wizard.training.simpo_gamma.label',  'wizard.training.simpo_gamma.hint',  'wizard.training.simpo_gamma.detail'));
        pane.appendChild(makeParamSlider('simpoBeta',   0.5,  4.0,  0.5,
          'wizard.training.simpo_beta.label',   'wizard.training.simpo_beta.hint',   'wizard.training.simpo_beta.detail'));
      } else if (state.trainerType === 'kto') {
        pane.appendChild(makeParamSlider('ktoBeta',     0.05, 0.5,  0.05,
          'wizard.training.kto_beta.label',     'wizard.training.kto_beta.hint',     'wizard.training.kto_beta.detail'));
      } else if (state.trainerType === 'grpo') {
        pane.appendChild(makeParamNumber('grpoNumGenerations',    1, 16, 1,
          'wizard.training.grpo_num_generations.label',    'wizard.training.grpo_num_generations.hint',    'wizard.training.grpo_num_generations.detail'));
        pane.appendChild(makeParamNumber('grpoMaxCompletionLength', 64, 2048, 32,
          'wizard.training.grpo_max_completion_length.label', 'wizard.training.grpo_max_completion_length.hint', 'wizard.training.grpo_max_completion_length.detail'));
        // GRPO reward model is optional; an empty value omits it from
        // the YAML and TRL falls back to the built-in
        // format/length-shaping reward.
        var rewardInput = el('input', {
          id: controlId('grpoRewardModel'),
          type: 'text',
          class: 'wizard-input',
          value: state.grpoRewardModel || '',
          placeholder: tr('wizard.training.grpo_reward_model.placeholder')
        });
        rewardInput.addEventListener('input', function () {
          state.grpoRewardModel = rewardInput.value.trim();
          persist();
          liveYaml();
        });
        var rewardRow = el('div', { class: 'wizard-row' }, [
          el('label', { for: controlId('grpoRewardModel'), class: 'wizard-row-label', text: tr('wizard.training.grpo_reward_model.label') }),
          rewardInput,
          el('span', { class: 'wizard-row-hint', text: tr('wizard.training.grpo_reward_model.hint') })
        ]);
        appendDetail(state, rewardRow, 'wizard.training.grpo_reward_model.detail');
        pane.appendChild(rewardRow);
      }
    }
  };

  /* Step 7: Evaluation + EU AI Act compliance */
  STEP_RENDERERS['compliance'] = function (pane, state, rerender, persist) {
    stepHeader(state, 'wizard.step.compliance.title', 'wizard.step.compliance.desc', 'wizard.step.compliance.tutorial')
      .forEach(function (n) { pane.appendChild(n); });

    function liveYaml() {
      var yamlOutput = document.querySelector('[data-wizard-yaml-output]');
      if (yamlOutput) yamlOutput.innerHTML = tintYaml(buildYaml(state));
    }

    // Risk classification
    var riskSelect = el('select', { id: controlId('riskClassification'), class: 'wizard-select' });
    [
      { id: 'unknown',       labelKey: 'wizard.risk.unknown' },
      { id: 'minimal-risk',  labelKey: 'wizard.risk.minimal' },
      { id: 'limited-risk',  labelKey: 'wizard.risk.limited' },
      { id: 'high-risk',     labelKey: 'wizard.risk.high' },
      { id: 'unacceptable',  labelKey: 'wizard.risk.unacceptable' }
    ].forEach(function (opt) {
      var option = el('option', { value: opt.id, text: tr(opt.labelKey) });
      if (state.riskClassification === opt.id) option.selected = true;
      riskSelect.appendChild(option);
    });
    riskSelect.addEventListener('change', function () {
      state.riskClassification = riskSelect.value;
      coerceForRisk(state);
      persist();
      rerender();
    });
    pane.appendChild(el('div', { class: 'wizard-row' }, [
      el('label', { for: controlId('riskClassification'), class: 'wizard-row-label', text: tr('wizard.risk.label') }),
      riskSelect,
      el('span', { class: 'wizard-row-hint', text: tr('wizard.risk.hint') })
    ]));

    // High-risk callout
    if (state.riskClassification === 'high-risk') {
      pane.appendChild(el('div', {
        class: 'wizard-callout',
        html: '<strong>' + tr('wizard.risk.high.callout.title') + '</strong> ' + tr('wizard.risk.high.callout.body')
      }));
    } else if (state.riskClassification === 'unacceptable') {
      pane.appendChild(el('div', {
        class: 'wizard-callout',
        html: '<strong>' + tr('wizard.risk.unacceptable.callout.title') + '</strong> ' + tr('wizard.risk.unacceptable.callout.body')
      }));
    }

    // Toggles. ``locked`` disables the input and adds a "required for risk
    // tier" badge — used for fields the EU AI Act forces under high-risk
    // / unacceptable tiers (Articles 9-17). ``reveals`` re-renders the step
    // for a toggle that shows or hides other fields.
    function makeToggle(field, labelKey, hintKey, locked, detailKey, reveals) {
      var input = el('input', { id: controlId(field), type: 'checkbox' });
      input.checked = state[field];
      if (locked) input.disabled = true;
      input.addEventListener('change', function () {
        state[field] = input.checked;
        persist();
        if (reveals) rerender();
        else liveYaml();
      });
      var labelEl = el('span', { class: 'wizard-toggle-label', text: tr(labelKey) });
      if (locked) {
        // The space keeps the badge a separate word in the accessible name;
        // the CSS margin alone does not.
        labelEl.appendChild(document.createTextNode(' '));
        labelEl.appendChild(el('span', {
          class: 'badge',
          text: tr('wizard.locked.badge'),
          style: 'margin-left: 0.4rem;'
        }));
      }
      var row = el('div', { class: 'wizard-row' }, [
        el('label', { class: 'wizard-toggle' }, [input, labelEl]),
        el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
      ]);
      appendDetail(state, row, detailKey);
      return row;
    }
    var highRisk = isStrictRisk(state);
    pane.appendChild(makeToggle('autoRevert', 'wizard.eval.auto_revert.label', 'wizard.eval.auto_revert.hint', false, 'wizard.eval.auto_revert.detail', true));

    if (state.autoRevert) {
      var lossInput = el('input', {
        id: controlId('maxAcceptableLoss'),
        type: 'number',
        class: 'wizard-input',
        min: '0.1',
        step: '0.1',
        value: String(state.maxAcceptableLoss)
      });
      lossInput.addEventListener('input', function () {
        var v = parseFloat(lossInput.value);
        if (!isNaN(v) && v > 0) {
          state.maxAcceptableLoss = v;
          persist();
          liveYaml();
        }
      });
      var lossRow = el('div', { class: 'wizard-row' }, [
        el('label', { for: controlId('maxAcceptableLoss'), class: 'wizard-row-label', text: tr('wizard.eval.max_loss.label') }),
        lossInput,
        el('span', { class: 'wizard-row-hint', text: tr('wizard.eval.max_loss.hint') })
      ]);
      appendDetail(state, lossRow, 'wizard.eval.max_loss.detail');
      pane.appendChild(lossRow);
    }

    pane.appendChild(makeToggle('safetyEval', 'wizard.eval.safety.label', 'wizard.eval.safety.hint', highRisk, 'wizard.eval.safety.detail'));
    pane.appendChild(makeToggle('humanApproval', 'wizard.compliance.approval.label', 'wizard.compliance.approval.hint', highRisk, 'wizard.compliance.approval.detail'));

    // Informational block: the audit log + Annex IV bundle are not YAML
    // toggles — they are produced as side-effects of the run. Surface
    // this fact instead of pretending the operator has a control they
    // don't actually have.
    pane.appendChild(el('div', {
      class: 'wizard-step-tutorial',
      html: '<strong>' + tr('wizard.compliance.always.title') + '</strong> ' + tr('wizard.compliance.always.body')
    }));

    // Annex IV §1 provider metadata. Surface ALWAYS (any tier benefits
    // from named provider info) but tag the inputs as "required for
    // high-risk" so the operator knows the obligation rises with the
    // tier. Empty values are valid; ComplianceMetadataConfig has
    // string defaults of "".
    // ``errorKey`` is the i18n key for the inline validation message
    // shown when the operator hits Next without filling this field.
    // For high-risk tiers all three Annex IV §1 fields are mandatory;
    // ``fieldHasError`` returns the key only after a failed Next so
    // the first visit to the step stays uncluttered.
    function makeMetaInput(field, labelKey, hintKey, placeholderKey, errorKey) {
      var errKey = errorKey ? fieldHasError(state, 'compliance', field) : null;
      var input = el('input', {
        id: controlId(field),
        type: 'text',
        class: 'wizard-input' + (errKey ? ' is-invalid' : ''),
        value: state[field] || '',
        placeholder: tr(placeholderKey)
      });
      var rowChildren = [
        el('label', { for: controlId(field), class: 'wizard-row-label' }, [
          tr(labelKey),
          // A separate word in the accessible name whatever the label's display.
          highRisk ? ' ' : null,
          highRisk ? el('span', { class: 'badge', text: tr('wizard.locked.badge'), style: 'margin-left: 0.4rem;' }) : null
        ]),
        input,
        el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
      ];
      if (errKey) {
        rowChildren.push(el('div', { class: 'wizard-error', role: 'alert', text: tr(errKey) }));
      }
      var row = el('div', { class: 'wizard-row' }, rowChildren);
      input.addEventListener('input', function () {
        state[field] = input.value;
        persist();
        liveYaml();
        if (errorKey) {
          var stillErr = validateStep(state, 'compliance').some(function (e) { return e.field === field; });
          if (!stillErr) {
            input.classList.remove('is-invalid');
            var errEl = row.querySelector('.wizard-error');
            if (errEl) errEl.remove();
          }
        }
      });
      return row;
    }
    if (highRisk) {
      pane.appendChild(makeMetaInput('providerName',     'wizard.compliance.provider_name.label',     'wizard.compliance.provider_name.hint',     'wizard.compliance.provider_name.placeholder',     'wizard.error.compliance.provider_name.required'));
      pane.appendChild(makeMetaInput('systemName',       'wizard.compliance.system_name.label',       'wizard.compliance.system_name.hint',       'wizard.compliance.system_name.placeholder',       'wizard.error.compliance.system_name.required'));
      pane.appendChild(makeMetaInput('intendedPurpose',  'wizard.compliance.intended_purpose.label',  'wizard.compliance.intended_purpose.hint',  'wizard.compliance.intended_purpose.placeholder',  'wizard.error.compliance.intended_purpose.required'));
    }

    // Article 9 Risk Assessment accordion. Open by default for strict
    // tiers (high-risk / unacceptable) where Article 9 documentation
    // is mandatory; collapsed otherwise as a reminder it exists.
    if (highRisk && state.openAccordions && state.openAccordions['risk_assessment'] === undefined) {
      state.openAccordions = state.openAccordions || {};
      state.openAccordions['risk_assessment'] = true;
    }
    pane.appendChild(makeAccordion(state, persist, 'risk_assessment',
      'wizard.risk_assessment.title', 'wizard.risk_assessment.hint', function (body) {

      // Shared factory for the three required textareas. Wires the
      // inline validation pattern: errKey appears only after a failed
      // Next; the error visual + alert message are removed live as
      // soon as the operator types something non-blank.
      function makeRequiredTextarea(field, rows, labelKey, hintKey, placeholderKey, detailKey, errorKey) {
        var errKey = errorKey ? fieldHasError(state, 'compliance', field) : null;
        var ta = el('textarea', {
          id: controlId(field),
          class: 'wizard-input' + (errKey ? ' is-invalid' : ''),
          rows: String(rows),
          placeholder: tr(placeholderKey)
        });
        ta.value = state[field] || '';
        var rowChildren = [
          el('label', { for: controlId(field), class: 'wizard-row-label', text: tr(labelKey) }),
          ta,
          el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
        ];
        if (errKey) {
          rowChildren.push(el('div', { class: 'wizard-error', role: 'alert', text: tr(errKey) }));
        }
        var row = el('div', { class: 'wizard-row' }, rowChildren);
        ta.addEventListener('input', function () {
          state[field] = ta.value;
          persist();
          liveYaml();
          if (errorKey) {
            var stillErr = validateStep(state, 'compliance').some(function (e) { return e.field === field; });
            if (!stillErr) {
              ta.classList.remove('is-invalid');
              var errEl = row.querySelector('.wizard-error');
              if (errEl) errEl.remove();
            }
          }
        });
        appendDetail(state, row, detailKey);
        return row;
      }

      // All three risk-assessment fields are required ONLY for strict
      // tiers (high-risk + unacceptable). For everyone else they stay
      // optional — the schema accepts empty strings.
      var requireRA = isStrictRisk(state);
      body.appendChild(makeRequiredTextarea('intendedUse', 2,
        'wizard.risk_assessment.intended_use.label',
        'wizard.risk_assessment.intended_use.hint',
        'wizard.risk_assessment.intended_use.placeholder',
        'wizard.risk_assessment.intended_use.detail',
        requireRA ? 'wizard.error.risk_assessment.intended_use.required' : null));
      body.appendChild(makeRequiredTextarea('foreseeableMisuse', 3,
        'wizard.risk_assessment.foreseeable_misuse.label',
        'wizard.risk_assessment.foreseeable_misuse.hint',
        'wizard.risk_assessment.foreseeable_misuse.placeholder',
        'wizard.risk_assessment.foreseeable_misuse.detail',
        requireRA ? 'wizard.error.risk_assessment.foreseeable_misuse.required' : null));
      body.appendChild(makeRequiredTextarea('mitigationMeasures', 3,
        'wizard.risk_assessment.mitigation_measures.label',
        'wizard.risk_assessment.mitigation_measures.hint',
        'wizard.risk_assessment.mitigation_measures.placeholder',
        'wizard.risk_assessment.mitigation_measures.detail',
        requireRA ? 'wizard.error.risk_assessment.mitigation_measures.required' : null));

      // vulnerable_groups_considered: checkbox
      var vgInput = el('input', { id: controlId('vulnerableGroupsConsidered'), type: 'checkbox' });
      vgInput.checked = !!state.vulnerableGroupsConsidered;
      vgInput.addEventListener('change', function () {
        state.vulnerableGroupsConsidered = vgInput.checked;
        persist();
        liveYaml();
      });
      var vgRow = el('div', { class: 'wizard-row' }, [
        el('label', { class: 'wizard-toggle' }, [
          vgInput,
          el('span', { class: 'wizard-toggle-label', text: tr('wizard.risk_assessment.vulnerable_groups.label') })
        ]),
        el('span', { class: 'wizard-row-hint', text: tr('wizard.risk_assessment.vulnerable_groups.hint') })
      ]);
      appendDetail(state, vgRow, 'wizard.risk_assessment.vulnerable_groups.detail');
      body.appendChild(vgRow);
    }));

    // Evaluation gates accordion — Benchmark + LLM Judge. Both are
    // optional quality gates layered on top of the Auto-revert / Loss
    // / Safety triplet that the main step exposes. Surfacing them in
    // an accordion keeps the main step uncluttered for first-time
    // users while making the full schema reachable for power users.
    pane.appendChild(makeAccordion(state, persist, 'eval_gates',
      'wizard.eval_gates.title', 'wizard.eval_gates.hint', function (body) {

      // ── Benchmark sub-section ──
      var benchInput = el('input', { id: controlId('benchmarkEnabled'), type: 'checkbox' });
      benchInput.checked = !!state.benchmarkEnabled;
      benchInput.addEventListener('change', function () {
        state.benchmarkEnabled = benchInput.checked;
        persist();
        rerender();
      });
      var benchToggleRow = el('div', { class: 'wizard-row' }, [
        el('label', { class: 'wizard-toggle' }, [
          benchInput,
          el('span', { class: 'wizard-toggle-label', text: tr('wizard.benchmark.enabled.label') })
        ]),
        el('span', { class: 'wizard-row-hint', text: tr('wizard.benchmark.enabled.hint') })
      ]);
      appendDetail(state, benchToggleRow, 'wizard.benchmark.enabled.detail');
      body.appendChild(benchToggleRow);

      if (state.benchmarkEnabled) {
        // benchmarkTasks is required when the benchmark gate is on —
        // an empty list makes the gate a no-op. Validation message
        // explains this so the operator knows why they can't advance.
        var benchTasksErrKey = fieldHasError(state, 'compliance', 'benchmarkTasks');
        var tasksTextarea = el('textarea', {
          id: controlId('benchmarkTasks'),
          class: 'wizard-input' + (benchTasksErrKey ? ' is-invalid' : ''),
          rows: '3',
          placeholder: 'arc_easy\nhellaswag\nmmlu'
        });
        tasksTextarea.value = state.benchmarkTasks || '';
        var tasksRowChildren = [
          el('label', { for: controlId('benchmarkTasks'), class: 'wizard-row-label', text: tr('wizard.benchmark.tasks.label') }),
          tasksTextarea,
          el('span', { class: 'wizard-row-hint', text: tr('wizard.benchmark.tasks.hint') })
        ];
        if (benchTasksErrKey) {
          tasksRowChildren.push(el('div', { class: 'wizard-error', role: 'alert', text: tr(benchTasksErrKey) }));
        }
        var tasksRow = el('div', { class: 'wizard-row' }, tasksRowChildren);
        tasksTextarea.addEventListener('input', function () {
          state.benchmarkTasks = tasksTextarea.value;
          persist();
          liveYaml();
          var stillErr = validateStep(state, 'compliance').some(function (e) { return e.field === 'benchmarkTasks'; });
          if (!stillErr) {
            tasksTextarea.classList.remove('is-invalid');
            var errEl = tasksRow.querySelector('.wizard-error');
            if (errEl) errEl.remove();
          }
        });
        body.appendChild(tasksRow);

        var minScoreInput = el('input', {
          id: controlId('benchmarkMinScore'),
          type: 'number',
          class: 'wizard-input',
          min: '0',
          max: '1',
          step: '0.01',
          placeholder: tr('wizard.benchmark.min_score.placeholder'),
          value: state.benchmarkMinScore || ''
        });
        minScoreInput.addEventListener('input', function () {
          state.benchmarkMinScore = minScoreInput.value;
          persist();
          liveYaml();
        });
        body.appendChild(el('div', { class: 'wizard-row' }, [
          el('label', { for: controlId('benchmarkMinScore'), class: 'wizard-row-label', text: tr('wizard.benchmark.min_score.label') }),
          minScoreInput,
          el('span', { class: 'wizard-row-hint', text: tr('wizard.benchmark.min_score.hint') })
        ]));
      }

      // ── LLM Judge sub-section ──
      var judgeInput = el('input', { id: controlId('judgeEnabled'), type: 'checkbox' });
      judgeInput.checked = !!state.judgeEnabled;
      judgeInput.addEventListener('change', function () {
        state.judgeEnabled = judgeInput.checked;
        persist();
        rerender();
      });
      var judgeToggleRow = el('div', { class: 'wizard-row', style: 'margin-top: 1.4rem;' }, [
        el('label', { class: 'wizard-toggle' }, [
          judgeInput,
          el('span', { class: 'wizard-toggle-label', text: tr('wizard.judge.enabled.label') })
        ]),
        el('span', { class: 'wizard-row-hint', text: tr('wizard.judge.enabled.hint') })
      ]);
      appendDetail(state, judgeToggleRow, 'wizard.judge.enabled.detail');
      body.appendChild(judgeToggleRow);

      if (state.judgeEnabled) {
        var judgeModelInput = el('input', {
          id: controlId('judgeModel'),
          type: 'text',
          class: 'wizard-input',
          placeholder: 'gpt-4o',
          value: state.judgeModel || ''
        });
        judgeModelInput.addEventListener('input', function () {
          state.judgeModel = judgeModelInput.value.trim();
          persist();
          liveYaml();
        });
        body.appendChild(el('div', { class: 'wizard-row' }, [
          el('label', { for: controlId('judgeModel'), class: 'wizard-row-label', text: tr('wizard.judge.model.label') }),
          judgeModelInput,
          el('span', { class: 'wizard-row-hint', text: tr('wizard.judge.model.hint') })
        ]));

        // Judge API key env var is required when the judge gate is on —
        // without it the runtime can't authenticate to the judge model.
        var judgeApiErrKey = fieldHasError(state, 'compliance', 'judgeApiKeyEnv');
        var judgeApiInput = el('input', {
          id: controlId('judgeApiKeyEnv'),
          type: 'text',
          class: 'wizard-input' + (judgeApiErrKey ? ' is-invalid' : ''),
          placeholder: 'OPENAI_API_KEY',
          value: state.judgeApiKeyEnv || ''
        });
        var judgeApiRowChildren = [
          el('label', { for: controlId('judgeApiKeyEnv'), class: 'wizard-row-label', text: tr('wizard.judge.api_key_env.label') }),
          judgeApiInput,
          el('span', { class: 'wizard-row-hint', text: tr('wizard.judge.api_key_env.hint') })
        ];
        if (judgeApiErrKey) {
          judgeApiRowChildren.push(el('div', { class: 'wizard-error', role: 'alert', text: tr(judgeApiErrKey) }));
        }
        var judgeApiRow = el('div', { class: 'wizard-row' }, judgeApiRowChildren);
        judgeApiInput.addEventListener('input', function () {
          state.judgeApiKeyEnv = judgeApiInput.value.trim();
          persist();
          liveYaml();
          var stillErr = validateStep(state, 'compliance').some(function (e) { return e.field === 'judgeApiKeyEnv'; });
          if (!stillErr) {
            judgeApiInput.classList.remove('is-invalid');
            var errEl = judgeApiRow.querySelector('.wizard-error');
            if (errEl) errEl.remove();
          }
        });
        body.appendChild(judgeApiRow);

        var judgeScoreInput = el('input', {
          id: controlId('judgeMinScore'),
          type: 'number',
          class: 'wizard-input',
          min: '1',
          max: '10',
          step: '0.5',
          value: String(state.judgeMinScore || 5.0)
        });
        judgeScoreInput.addEventListener('input', function () {
          var v = parseFloat(judgeScoreInput.value);
          if (!isNaN(v) && v >= 1 && v <= 10) {
            state.judgeMinScore = v;
            persist();
            liveYaml();
          }
        });
        body.appendChild(el('div', { class: 'wizard-row' }, [
          el('label', { for: controlId('judgeMinScore'), class: 'wizard-row-label', text: tr('wizard.judge.min_score.label') }),
          judgeScoreInput,
          el('span', { class: 'wizard-row-hint', text: tr('wizard.judge.min_score.hint') })
        ]));
      }
    }));

  };

  /* Step 8: Advanced operational settings — notifications, post-market
     monitoring, synthetic data generation, retention horizons. All
     optional; the operator can simply Next through this step if none of
     these features apply. Lifted out of the compliance step in v3 so
     each block stays digestible (compliance was getting too tall to
     scan in one viewport). */
  STEP_RENDERERS['operations'] = function (pane, state, rerender, persist) {
    stepHeader(state, 'wizard.step.operations.title', 'wizard.step.operations.desc', 'wizard.step.operations.tutorial')
      .forEach(function (n) { pane.appendChild(n); });

    function liveYaml() {
      var yamlOutput = document.querySelector('[data-wizard-yaml-output]');
      if (yamlOutput) yamlOutput.innerHTML = tintYaml(buildYaml(state));
    }

    // Webhook (Slack/Teams/Discord) accordion.
    pane.appendChild(makeAccordion(state, persist, 'webhook',
      'wizard.webhook.title', 'wizard.webhook.hint', function (body) {

      // Enable toggle. When off, the rest of the accordion's inputs
      // stay collapsed and the YAML omits the webhook block entirely.
      // When on, the URL is required (validateStep paints an inline
      // error if blank when the operator hits Next).
      var enabledInput = el('input', { id: controlId('webhookEnabled'), type: 'checkbox' });
      enabledInput.checked = !!state.webhookEnabled;
      enabledInput.addEventListener('change', function () {
        state.webhookEnabled = enabledInput.checked;
        persist();
        rerender();
      });
      var enabledRow = el('div', { class: 'wizard-row' }, [
        el('label', { class: 'wizard-toggle' }, [
          enabledInput,
          el('span', { class: 'wizard-toggle-label', text: tr('wizard.webhook.enabled.label') })
        ]),
        el('span', { class: 'wizard-row-hint', text: tr('wizard.webhook.enabled.hint') })
      ]);
      appendDetail(state, enabledRow, 'wizard.webhook.enabled.detail');
      body.appendChild(enabledRow);

      if (!state.webhookEnabled) return;

      // URL field — required when webhookEnabled is true. Validation
      // is wired through fieldHasError so the inline error appears
      // only after the operator tried to advance past this step.
      var webhookUrlErrKey = fieldHasError(state, 'operations', 'webhookUrl');
      var urlInput = el('input', {
        id: controlId('webhookUrl'),
        type: 'text',
        class: 'wizard-input' + (webhookUrlErrKey ? ' is-invalid' : ''),
        placeholder: tr('wizard.webhook.url.placeholder'),
        value: state.webhookUrl || ''
      });
      urlInput.addEventListener('input', function () {
        state.webhookUrl = urlInput.value.trim();
        persist();
        liveYaml();
        // Drop the error visual once the field becomes non-empty.
        var stillErr = validateStep(state, 'operations').some(function (e) { return e.field === 'webhookUrl'; });
        if (!stillErr) {
          urlInput.classList.remove('is-invalid');
          var errEl = urlRow.querySelector('.wizard-error');
          if (errEl) errEl.remove();
        }
      });
      var urlRowChildren = [
        el('label', { for: controlId('webhookUrl'), class: 'wizard-row-label', text: tr('wizard.webhook.url.label') }),
        urlInput,
        el('span', { class: 'wizard-row-hint', text: tr('wizard.webhook.url.hint') })
      ];
      if (webhookUrlErrKey) {
        urlRowChildren.push(el('div', { class: 'wizard-error', role: 'alert', text: tr(webhookUrlErrKey) }));
      }
      var urlRow = el('div', { class: 'wizard-row' }, urlRowChildren);
      appendDetail(state, urlRow, 'wizard.webhook.url.detail');
      body.appendChild(urlRow);

      function makeWebhookToggle(field, labelKey) {
        var cb = el('input', { id: controlId(field), type: 'checkbox' });
        cb.checked = !!state[field];
        cb.addEventListener('change', function () {
          state[field] = cb.checked;
          persist();
          liveYaml();
        });
        return el('div', { class: 'wizard-row' }, [
          el('label', { class: 'wizard-toggle' }, [
            cb,
            el('span', { class: 'wizard-toggle-label', text: tr(labelKey) })
          ])
        ]);
      }
      body.appendChild(makeWebhookToggle('webhookNotifyStart',   'wizard.webhook.notify_start.label'));
      body.appendChild(makeWebhookToggle('webhookNotifySuccess', 'wizard.webhook.notify_success.label'));
      body.appendChild(makeWebhookToggle('webhookNotifyFailure', 'wizard.webhook.notify_failure.label'));
    }));

    // Declarative external monitoring plan (Article 72 / 17(1)(h)).
    pane.appendChild(makeAccordion(state, persist, 'monitoring',
      'wizard.monitoring.title', 'wizard.monitoring.hint', function (body) {
      var monInput = el('input', { id: controlId('monitoringEnabled'), type: 'checkbox' });
      monInput.checked = !!state.monitoringEnabled;
      monInput.addEventListener('change', function () {
        state.monitoringEnabled = monInput.checked;
        persist();
        rerender();
      });
      var monLabelEl = el('span', { class: 'wizard-toggle-label', text: tr('wizard.monitoring.enabled.label') });
      var monRow = el('div', { class: 'wizard-row' }, [
        el('label', { class: 'wizard-toggle' }, [monInput, monLabelEl]),
        el('span', { class: 'wizard-row-hint', text: tr('wizard.monitoring.enabled.hint') })
      ]);
      appendDetail(state, monRow, 'wizard.monitoring.enabled.detail');
      body.appendChild(monRow);

      var endpointShown = !!state.monitoringEnabled;
      if (endpointShown) {
        var monEndpointErr = fieldHasError(state, 'operations', 'monitoringEndpoint');
        var epInput = el('input', {
          id: controlId('monitoringEndpoint'),
          type: 'text',
          class: 'wizard-input' + (monEndpointErr ? ' is-invalid' : ''),
          placeholder: tr('wizard.monitoring.endpoint.placeholder'),
          value: state.monitoringEndpoint || ''
        });
        var epRowChildren = [
          el('label', { for: controlId('monitoringEndpoint'), class: 'wizard-row-label', text: tr('wizard.monitoring.endpoint.label') }),
          epInput,
          el('span', { class: 'wizard-row-hint', text: tr('wizard.monitoring.endpoint.hint') })
        ];
        var epRow = el('div', { class: 'wizard-row' }, epRowChildren);
        if (monEndpointErr) epRow.appendChild(el('div', { class: 'wizard-error', role: 'alert', text: tr(monEndpointErr) }));
        epInput.addEventListener('input', function () {
          state.monitoringEndpoint = epInput.value.trim();
          persist();
          liveYaml();
          if (!fieldHasError(state, 'operations', 'monitoringEndpoint')) {
            epInput.classList.remove('is-invalid');
            var error = epRow.querySelector('.wizard-error');
            if (error) error.remove();
          }
        });
        body.appendChild(epRow);

        var intervalError = fieldHasError(state, 'operations', 'monitoringCheckIntervalHours');
        var intervalInput = el('input', {
          id: controlId('monitoringCheckIntervalHours'), type: 'number', min: '1', max: '720', step: '1',
          class: 'wizard-input' + (intervalError ? ' is-invalid' : ''),
          value: String(state.monitoringCheckIntervalHours)
        });
        intervalInput.addEventListener('input', function () {
          state.monitoringCheckIntervalHours = Number(intervalInput.value);
          persist();
          liveYaml();
          if (!fieldHasError(state, 'operations', 'monitoringCheckIntervalHours')) {
            intervalInput.classList.remove('is-invalid');
            var error = intervalRow.querySelector('.wizard-error');
            if (error) error.remove();
          }
        });
        var intervalRow = el('div', { class: 'wizard-row' }, [
          el('label', { for: controlId('monitoringCheckIntervalHours'), class: 'wizard-row-label', text: tr('wizard.monitoring.interval.label') }),
          intervalInput
        ]);
        if (intervalError) intervalRow.appendChild(el('div', { class: 'wizard-error', role: 'alert', text: tr(intervalError) }));
        body.appendChild(intervalRow);

        var meSelect = el('select', { id: controlId('metricsExport'), class: 'wizard-select' });
        [
          { id: 'none',           labelKey: 'wizard.monitoring.metrics_export.none' },
          { id: 'prometheus',     labelKey: 'wizard.monitoring.metrics_export.prometheus' },
          { id: 'datadog',        labelKey: 'wizard.monitoring.metrics_export.datadog' },
          { id: 'custom_webhook', labelKey: 'wizard.monitoring.metrics_export.custom' }
        ].forEach(function (opt) {
          var option = el('option', { value: opt.id, text: tr(opt.labelKey) });
          if (state.metricsExport === opt.id) option.selected = true;
          meSelect.appendChild(option);
        });
        meSelect.addEventListener('change', function () {
          state.metricsExport = meSelect.value;
          persist();
          liveYaml();
        });
        body.appendChild(el('div', { class: 'wizard-row' }, [
          el('label', { for: controlId('metricsExport'), class: 'wizard-row-label', text: tr('wizard.monitoring.metrics_export.label') }),
          meSelect
        ]));

        var driftCb = el('input', { id: controlId('alertOnDrift'), type: 'checkbox' });
        driftCb.checked = !!state.alertOnDrift;
        driftCb.addEventListener('change', function () {
          state.alertOnDrift = driftCb.checked;
          persist();
          liveYaml();
        });
        body.appendChild(el('div', { class: 'wizard-row' }, [
          el('label', { class: 'wizard-toggle' }, [
            driftCb,
            el('span', { class: 'wizard-toggle-label', text: tr('wizard.monitoring.alert_on_drift.label') })
          ]),
          el('span', { class: 'wizard-row-hint', text: tr('wizard.monitoring.alert_on_drift.hint') })
        ]));
      }
    }));

    // Synthetic data generation accordion.
    pane.appendChild(makeAccordion(state, persist, 'synthetic',
      'wizard.synthetic.title', 'wizard.synthetic.hint', function (body) {
      var synInput = el('input', { id: controlId('syntheticEnabled'), type: 'checkbox' });
      synInput.checked = !!state.syntheticEnabled;
      synInput.addEventListener('change', function () {
        state.syntheticEnabled = synInput.checked;
        persist();
        rerender();
      });
      var synRow = el('div', { class: 'wizard-row' }, [
        el('label', { class: 'wizard-toggle' }, [
          synInput,
          el('span', { class: 'wizard-toggle-label', text: tr('wizard.synthetic.enabled.label') })
        ]),
        el('span', { class: 'wizard-row-hint', text: tr('wizard.synthetic.enabled.hint') })
      ]);
      appendDetail(state, synRow, 'wizard.synthetic.enabled.detail');
      body.appendChild(synRow);

      if (state.syntheticEnabled) {
        // Synthetic data generation has THREE required inputs once
        // the feature is enabled: teacher model, seed prompts file,
        // and the API key env var. None have a sensible default that
        // ForgeLM can guess, so all three are validated here. The
        // factory below mirrors the validateStep ↔ inline-error
        // wiring used by webhook.url and monitoring.endpoint.
        function makeRequiredText(field, labelKey, hintKey, placeholderKey, errorKey) {
          var errKey = fieldHasError(state, 'operations', field);
          var inp = el('input', {
            id: controlId(field),
            type: 'text',
            class: 'wizard-input' + (errKey ? ' is-invalid' : ''),
            placeholder: placeholderKey ? tr(placeholderKey) : '',
            value: state[field] || ''
          });
          var rowChildren = [
            el('label', { for: controlId(field), class: 'wizard-row-label', text: tr(labelKey) }),
            inp,
            el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
          ];
          if (errKey) {
            rowChildren.push(el('div', { class: 'wizard-error', role: 'alert', text: tr(errKey) }));
          }
          var row = el('div', { class: 'wizard-row' }, rowChildren);
          inp.addEventListener('input', function () {
            state[field] = inp.value.trim();
            persist();
            liveYaml();
            var stillErr = validateStep(state, 'operations').some(function (e) { return e.field === field; });
            if (!stillErr) {
              inp.classList.remove('is-invalid');
              var errEl = row.querySelector('.wizard-error');
              if (errEl) errEl.remove();
            }
          });
          return row;
        }
        body.appendChild(makeRequiredText('syntheticTeacherModel',
          'wizard.synthetic.teacher_model.label',
          'wizard.synthetic.teacher_model.hint',
          null,
          'wizard.error.synthetic.teacher_model.required'));
        body.appendChild(makeRequiredText('syntheticSeedFile',
          'wizard.synthetic.seed_file.label',
          'wizard.synthetic.seed_file.hint',
          'wizard.synthetic.seed_file.placeholder',
          'wizard.error.synthetic.seed_file.required'));
        body.appendChild(makeRequiredText('syntheticApiKeyEnv',
          'wizard.synthetic.api_key_env.label',
          'wizard.synthetic.api_key_env.hint',
          null,
          'wizard.error.synthetic.api_key_env.required'));
      }
    }));

    // Retention horizons (GDPR Art. 5+17) accordion.
    pane.appendChild(makeAccordion(state, persist, 'retention',
      'wizard.retention.title', 'wizard.retention.hint', function (body) {
      function makeNumberRow(field, labelKey, hintKey) {
        var inp = el('input', {
          id: controlId(field),
          type: 'number',
          class: 'wizard-input',
          min: '0',
          step: '1',
          value: String(state[field])
        });
        inp.addEventListener('input', function () {
          var v = parseInt(inp.value, 10);
          if (!isNaN(v) && v >= 0) {
            state[field] = v;
            persist();
            liveYaml();
          }
        });
        return el('div', { class: 'wizard-row' }, [
          el('label', { for: controlId(field), class: 'wizard-row-label', text: tr(labelKey) }),
          inp,
          el('span', { class: 'wizard-row-hint', text: tr(hintKey) })
        ]);
      }
      body.appendChild(makeNumberRow('retentionAuditLog',
        'wizard.retention.audit_log.label', 'wizard.retention.audit_log.hint'));
      body.appendChild(makeNumberRow('retentionStaging',
        'wizard.retention.staging.label', 'wizard.retention.staging.hint'));
      body.appendChild(makeNumberRow('retentionEphemeral',
        'wizard.retention.ephemeral.label', 'wizard.retention.ephemeral.hint'));
      body.appendChild(makeNumberRow('retentionRawDocuments',
        'wizard.retention.raw_documents.label', 'wizard.retention.raw_documents.hint'));

      // ``enforce`` is a 3-way Literal (log_only | warn_on_excess |
      // block_on_excess) — surfaced as a select. Default ``log_only``
      // is the safe choice; ``block_on_excess`` is for regulated CI
      // gates where silent extension of retention horizons is the
      // bug we're trying to prevent.
      var enforceSelect = el('select', { id: controlId('retentionEnforce'), class: 'wizard-select' });
      [
        { id: 'log_only',        labelKey: 'wizard.retention.enforce.log_only' },
        { id: 'warn_on_excess',  labelKey: 'wizard.retention.enforce.warn' },
        { id: 'block_on_excess', labelKey: 'wizard.retention.enforce.block' }
      ].forEach(function (opt) {
        var option = el('option', { value: opt.id, text: tr(opt.labelKey) });
        if (state.retentionEnforce === opt.id) option.selected = true;
        enforceSelect.appendChild(option);
      });
      enforceSelect.addEventListener('change', function () {
        state.retentionEnforce = enforceSelect.value;
        persist();
        liveYaml();
      });
      body.appendChild(el('div', { class: 'wizard-row' }, [
        el('label', { for: controlId('retentionEnforce'), class: 'wizard-row-label', text: tr('wizard.retention.enforce.label') }),
        enforceSelect,
        el('span', { class: 'wizard-row-hint', text: tr('wizard.retention.enforce.hint') })
      ]));
    }));
  };

  /* Step 7: Review + download */
  STEP_RENDERERS['review'] = function (pane, state, rerender, persist) {
    pane.appendChild(el('h2', { class: 'wizard-step-title', id: 'wizard-title', text: tr('wizard.step.review.title') }));
    pane.appendChild(el('p', { class: 'wizard-step-desc', text: tr('wizard.step.review.desc') }));
    pane.appendChild(el('div', { class: 'wizard-step-tutorial', text: tr('wizard.step.review.tutorial') }));

    var yamlText = buildYaml(state);

    // Action buttons row
    var actions = el('div', { class: 'wizard-actions' });

    var copyBtn = el('button', {
      type: 'button',
      class: 'btn btn-primary'
    }, [
      el('svg', { width: '16', height: '16', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', html: '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>' }),
      el('span', { text: tr('wizard.review.copy') })
    ]);
    copyBtn.addEventListener('click', function () {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(yamlText).then(function () {
          copyBtn.classList.add('is-copied');
          var label = copyBtn.querySelector('span');
          if (label) {
            var prev = label.textContent;
            label.textContent = tr('common.copy.done');
            setTimeout(function () {
              copyBtn.classList.remove('is-copied');
              label.textContent = prev;
            }, 1600);
          }
        });
      }
    });
    actions.appendChild(copyBtn);

    var downloadBtn = el('button', {
      type: 'button',
      class: 'btn btn-secondary'
    }, [
      el('svg', { width: '16', height: '16', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', html: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line>' }),
      el('span', { text: tr('wizard.review.download') })
    ]);
    downloadBtn.addEventListener('click', function () {
      var blob = new Blob([yamlText], { type: 'text/yaml' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'quickstart-generated.yaml';
      a.click();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
    actions.appendChild(downloadBtn);

    var resetBtn = el('button', {
      type: 'button',
      class: 'btn btn-ghost'
    }, [
      el('svg', { width: '16', height: '16', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', html: '<polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>' }),
      el('span', { text: tr('wizard.review.reset') })
    ]);
    resetBtn.addEventListener('click', function () {
      if (window.confirm(tr('wizard.review.reset.confirm'))) {
        clearState();
        var fresh = defaultState();
        Object.keys(fresh).forEach(function (k) { state[k] = fresh[k]; });
        rerender();
      }
    });
    actions.appendChild(resetBtn);

    pane.appendChild(actions);

    // Run snippet
    var snippet = el('pre', { class: 'wizard-run-snippet' }, [
      el('span', { class: 'prompt', text: '$ ' }),
      el('span', { text: 'forgelm ' }),
      el('span', { class: 'flag', text: '--config ' }),
      el('span', { class: 'arg', text: 'quickstart-generated.yaml ' }),
      el('span', { class: 'flag', text: '--dry-run' })
    ]);
    pane.appendChild(el('p', {
      class: 'wizard-step-desc',
      style: 'margin-top: 1.4rem; font-size: 0.86rem;',
      text: tr('wizard.review.run_label')
    }));
    pane.appendChild(snippet);
  };

  /* ── Boot ─────────────────────────────────────────────── */

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initWizard);
  } else {
    initWizard();
  }
})();
