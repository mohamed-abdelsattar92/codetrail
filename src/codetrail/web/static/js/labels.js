// Translated text for the page scripts, from a JSON block the server renders in the reader's language: each phrase
// marks the values it takes as `{name}`, and a counted phrase comes as its catalog's form for sample numbers.

export class Labels {
  constructor(data) {
    this.labels = data.labels;
    this.rules = new Intl.PluralRules(data.lang);
    this.numbers = new Intl.NumberFormat(`${data.lang}-u-nu-latn`);
  }

  number(value) {
    return this.numbers.format(value);
  }

  fill(template, values) {
    return template.replace(/\{(\w+)\}/g, (match, key) => (key in values ? String(values[key]) : match));
  }

  // The catalog's phrase for a sample number in the same plural category as the value.
  count(key, value, extra = {}) {
    const forms = this.labels[key];
    const category = this.rules.select(value);
    const samples = Object.keys(forms).map(Number).sort((a, b) => a - b);
    const sample = samples.find((number) => this.rules.select(number) === category) ?? samples[samples.length - 1];
    return this.fill(forms[String(sample)], { count: this.number(value), ...extra });
  }

  text(key, values = {}) {
    return this.fill(this.labels[key], values);
  }

  month(time) {
    const date = new Date(time);
    return `${this.labels.months[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
  }
}
