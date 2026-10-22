/**
 * FORGE API Client (Vanilla JS)
 */
const api = {
  async req(url, options = {}) {
    const res = await fetch(url, {
      ...options,
      headers: {
        'Accept': 'application/json',
        ...(options.headers || {})
      }
    });
    if (!res.ok) {
      let errText = res.statusText;
      try {
        const errJson = await res.json();
        errText = errJson.detail || JSON.stringify(errJson);
      } catch (e) {}
      throw new Error(errText);
    }
    return res.json();
  },

  async getState() {
    return this.req('/api/state');
  },

  async getQuestions() {
    return this.req('/api/survey/questions');
  },

  async saveSurveyAnswers(answers, step = null) {
    return this.req('/api/survey/answers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers, step })
    });
  },

  async rankProjects(answers = null, limit = 5, profileInputs = {}) {
    return this.req('/api/survey/rank', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers, limit, ...profileInputs })
    });
  },

  async searchProjects(query) {
    return this.req(`/api/projects/search?q=${encodeURIComponent(query)}`);
  },

  async sendRecommendationFeedback(projectId, signal, context = {}) {
    return this.req('/api/survey/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, signal, context })
    });
  },

  async uploadResume(file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/resume/upload', {
      method: 'POST',
      body: fd
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Resume upload failed');
    }
    return res.json();
  },

  async confirmResumeFactor(group, index, confirmed) {
    return this.req('/api/resume/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group, index, confirmed })
    });
  },

  async matchIdea(text) {
    return this.req('/api/idea/match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });
  },

  async getCompetitionEvents() {
    return this.req('/api/competition/events');
  },

  async getCompetitionProjects(event) {
    return this.req(`/api/competition/projects?event=${encodeURIComponent(event)}`);
  },

  async getCatalog() {
    return this.req('/api/bom/catalog');
  },
  async getStarProjects() {
    return this.req('/api/stars');
  },

  async getBom() {
    return this.req('/api/bom');
  },

  async generateBom(params) {
    return this.req('/api/bom/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params)
    });
  },

  streamSourcing(onLog, onComplete) {
    const es = new EventSource('/api/bom/sourcing/stream');
    es.addEventListener('log', (e) => {
      try {
        const data = JSON.parse(e.data);
        onLog(data.text);
      } catch (err) {
        onLog(e.data);
      }
    });
    es.addEventListener('complete', (e) => {
      es.close();
      onComplete();
    });
    es.onerror = () => {
      es.close();
      onComplete();
    };
    return es;
  },

  async setChoice(action, part_id = null, url = null, price = null, name = null) {
    return this.req('/api/bom/choice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, part_id, url, price, name })
    });
  },

  async getBill() {
    return this.req('/api/bom/bill');
  },

  async getLedger() {
    return this.req('/api/bom/ledger');
  },

  async getWiring() {
    return this.req('/api/wiring');
  },

  async optimizeDelivery() {
    return this.req('/api/delivery/optimize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
  },

  async applyDeliveryStrategy(strategy) {
    return this.req(`/api/delivery/apply?strategy=${encodeURIComponent(strategy)}`, {
      method: 'POST'
    });
  },

  async evaluateSwap(role, swap_part_id, staged_fixes = []) {
    return this.req('/api/optimizer/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, swap_part_id, staged_fixes })
    });
  },

  async applySwap(role, swap_part_id, staged_fixes = []) {
    return this.req('/api/optimizer/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, swap_part_id, staged_fixes })
    });
  },

  async applySafetyFix(fix_id) {
    return this.req('/api/optimizer/apply-fix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fix_id })
    });
  },

  async getOptimizerCandidates(role) {
    return this.req(`/api/optimizer/candidates?role=${encodeURIComponent(role)}`);
  },

  async resetSwaps() {
    return this.req('/api/optimizer/reset', { method: 'POST' });
  },

  async getSpecs() {
    return this.req('/api/research/specs');
  },

  async compareSpecs(partName, compareWith = []) {
    return this.req('/api/research/specs/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ part_name: partName, compare_with: compareWith })
    });
  },

  async fetchMissingSpecs() {
    return this.req('/api/research/specs/fetch-missing', {
      method: 'POST'
    });
  },

  async getResearchGuides() {
    return this.req('/api/research/guides');
  },

  async researchGuides(projectLabel = null) {
    return this.req('/api/research/guides', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_label: projectLabel })
    });
  }
};

