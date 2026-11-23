"use strict";

const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="game-token"]').content;
const stageLabels = {
  potential: "Потенциально", pending: "Ожидается к выплате",
  confirmed: "Подтверждено", withdrawn: "Реально выведено",
};
const nextStage = {potential: "pending", pending: "confirmed", confirmed: "withdrawn"};
const progressLabels = {
  start: "Записать начало", pending: "Записать завершение",
  confirmed: "Записать начисление", withdrawn: "Записать вывод",
};
const currencies = ["EUR", "USD", "GBP", "BTC", "LTC", "SOL", "USDC", "POINTS"];
const labels = {unknown: "Неизвестно", yes: "Да", no: "Нет"};
const deviceNames = {android: "Android", ios: "iOS", pc: "ПК", any: "Любое устройство", unknown: "Устройство неизвестно"};
let state;
let activeView = "offers";
let sourceChecks = {};
let currentOffer = null;
let currentProgress;
let previewURL;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function link(href, text) {
  const node = el("a", text);
  node.href = href;
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
}

function button(text, onClick, kind = "secondary compact") {
  const node = el("button", text, "button " + kind);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function options(select, entries) {
  for (const [value, title] of entries) {
    const option = el("option", title);
    option.value = value;
    select.append(option);
  }
}

function feedback(node, message) {
  node.textContent = message || "";
  node.hidden = !message;
}

async function api(path, data) {
  const options = data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Game-Token": token},
    body: JSON.stringify(data),
  };
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Запрос не выполнен.");
  return payload;
}

function setView(view) {
  activeView = ["offers", "journal", "sources"].includes(view) ? view : "offers";
  document.querySelectorAll(".view").forEach((section) => { section.hidden = section.id !== activeView; });
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.view === activeView) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  $("page-title").textContent = {offers: "Игровые задания", journal: "Журнал проверки", sources: "Источники"}[activeView];
  if (state) render();
}

function money(value, currency) {
  if (value === null || value === undefined) return "Не указано";
  const amount = String(value).replace(".", ",");
  return amount + " " + (currency === "POINTS" ? "баллов" : currency);
}

function metric(label, value) {
  const group = el("div", undefined, "metric");
  group.append(el("small", label), el("strong", value));
  return group;
}

function conditions(item, platform) {
  const details = el("details", undefined, "details");
  details.append(el("summary", "Условия и следующий шаг"));
  const list = el("ul");
  const feeModes = {unknown: "неизвестна", none: "нет", deducted: "из выплаты", upfront: "авансом"};
  const fee = feeModes[item.fee_mode] + (item.fee !== null ? " · " + money(item.fee, item.currency) : "");
  const facts = [
    "Германия — " + labels[item.germany], "Депозит — " + labels[item.deposit],
    "Обязательные покупки — " + labels[item.purchase], "KYC — " + labels[item.kyc],
    "Комиссия — " + fee, "Минимум — " + money(item.minimum, item.currency),
    "Способ вывода — " + (item.payout_method || "не указан"),
    item.probability !== null ? "Вероятность по вашей оценке — " + item.probability + "%" : "Вероятность не оценена",
    item.minutes_min ? "Время по вашей оценке — " + item.minutes_min + "–" + item.minutes_max + " мин" : "Время не оценено",
  ];
  if (item.days) facts.push("Дней с установки — " + item.days);
  if (item.currency !== "EUR" && item.rate_eur) facts.push("Ручной курс — " + money(item.rate_eur, "EUR"));
  if (item.cost !== null) facts.push("Начальные расходы — " + money(item.cost, item.currency));
  if (item.notes) facts.push(item.notes);
  if (item.analysis.reasons.length) facts.push(...item.analysis.reasons);
  for (const fact of facts) list.append(el("li", fact));
  details.append(list, el("p", platform.help, "muted"));
  if (item.analysis.eligible) {
    const next = item.milestones.find((step) => step.state === "potential");
    const guidance = next
      ? "Следующий оплачиваемый этап — " + next.label + ". После ручного выполнения проверьте зачёт на площадке."
      : "Сверьте начисления в личном кабинете. Записывайте вывод по фактически полученной сумме.";
    details.append(el("p", guidance));
  }
  if (item.screenshot) details.append(link("/api/picture/" + item.id, "Локальный скриншот"));
  return details;
}

function card(item) {
  const panel = el("article", undefined, "offer");
  const platform = state.platforms[item.platform];
  const evaluation = item.analysis;
  const head = el("div", undefined, "offer-head");
  const titles = el("div");
  titles.append(el("h3", item.title), el("p", platform.name + " · " + deviceNames[item.device], "card-label"));
  head.append(titles, button("Изменить", () => showOffer(item)));
  const badges = el("div", undefined, "badges");
  badges.append(el("span", evaluation.eligible ? "Сверено вами" : "Требует проверки",
                   "pill " + (evaluation.eligible ? "good" : "review")));
  if (evaluation.zero_cost) badges.append(el("span", "Без вложений", "pill good"));
  badges.append(el("span", "Риск · " + ["низкий", "умеренный", "повышенный", "высокий"][evaluation.risk], "pill"));
  const metrics = el("div", undefined, "metrics");
  metrics.append(metric("Награда по условиям", money(evaluation.gross, item.currency)),
                 metric("Оценка EUR/час", evaluation.rates ? evaluation.rates.join("–").replaceAll(".", ",") : "Нет оценки"),
                 metric("Дедлайн", evaluation.deadline || (item.days ? "От установки" : "Не указан")));
  const steps = el("ol", undefined, "steps");
  for (const step of item.milestones) {
    const row = el("li", undefined, "step");
    const desc = el("div");
    desc.append(el("strong", step.label), el("p", stageLabels[step.state] + " · " +
      money(step.state === "potential" ? step.quoted : step.reported, step.currency), "muted"));
    row.append(desc);
    if (nextStage[step.state]) {
      const next = nextStage[step.state];
      row.append(button(progressLabels[next], () => showProgress(item, next, step)));
    }
    steps.append(row);
  }
  const foot = el("div", undefined, "offer-foot");
  foot.append(link(item.url, "Страница площадки"));
  if (item.started) foot.append(el("span", "Начато " + item.started, "muted"));
  else foot.append(button(progressLabels.start, () => showProgress(item, "start")));
  panel.append(head, badges, metrics, conditions(item, platform), steps, foot);
  return panel;
}

function render() {
  if (activeView === "offers") {
    $("totals").replaceChildren(...Object.entries(stageLabels).map(([key, label]) => {
      const amount = Object.entries(state.summary[key]).map(([c, v]) => money(v, c)).join(" · ");
      const tile = el("article", undefined, "total");
      tile.append(el("p", label, "caption"), el("strong", amount || "0,00 USD", "sum"));
      return tile;
    }));
    const query = $("search").value.trim().toLocaleLowerCase("ru");
    const filtered = state.offers.filter((item) =>
      (!$("platform-filter").value || item.platform === $("platform-filter").value) &&
      (!$("zero-filter").checked || item.analysis.zero_cost) &&
      (!$("ready-filter").checked || item.analysis.eligible) &&
      (!query || [item.title, state.platforms[item.platform].name, ...item.milestones.map((m) => m.label)]
        .join(" ").toLocaleLowerCase("ru").includes(query)));
    $("match-count").textContent = "Показано " + filtered.length + " из " + state.offers.length;
    $("offer-list").replaceChildren(...filtered.map(card));
    $("empty").hidden = filtered.length > 0;
    $("empty-title").textContent = state.offers.length ? "Совпадений нет" : "Пока нет офферов";
    $("empty-hint").textContent = state.offers.length
      ? "Измените фильтры или добавьте другую игру."
      : "Перенесите требования из личного кабинета или импортируйте JSON.";
  } else if (activeView === "journal") {
    const entries = state.journal.filter((e) => e.reasons.length).map((entry) => {
      const row = el("article", undefined, "journal-entry");
      const item = state.offers.find((offer) => offer.id === entry.offer_id);
      row.append(el("strong", item?.title || "Оффер " + entry.offer_id),
        el("p", new Date(entry.checked).toLocaleString("ru-RU"), "muted"));
      const reasons = el("ul");
      for (const reason of entry.reasons) reasons.append(el("li", reason));
      row.append(reasons);
      return row;
    });
    $("journal-list").replaceChildren(...(entries.length ? entries : [el("p", "Записей об отклонении пока нет.", "muted")]));
  } else {
    $("source-list").replaceChildren(...Object.entries(state.platforms).map(([id, platform]) => {
      const panel = el("article", undefined, "source");
      panel.append(el("h3", platform.name), el("p", platform.help, "muted"),
        el("p", sourceChecks[id] || "Проверка доступа ещё не запускалась", "card-label"),
        link(platform.home, "Открыть площадку"), link(platform.rules, "Справка"));
      if (platform.payout) panel.append(link(platform.payout, "Условия выплат"));
      return panel;
    }));
  }
}

async function refresh() {
  state = await api("/api/state");
  render();
}

function addStep(label = "", amount = "") {
  const host = $("milestone-inputs");
  if (host.children.length >= 30) return;
  const row = el("div", undefined, "milestone-row");
  const nameLabel = el("label", "Условие этапа");
  const name = el("input");
  name.maxLength = 160;
  name.required = true;
  name.value = label;
  const amountLabel = el("label", "Награда");
  const number = el("input");
  number.inputMode = "decimal";
  number.value = amount ?? "";
  nameLabel.append(name);
  amountLabel.append(number);
  const remove = button("Удалить", () => {
    if (host.children.length > 1) row.remove();
  });
  row.append(nameLabel, amountLabel, remove);
  host.append(row);
}

function clearPreview() {
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = null;
  $("preview").removeAttribute("src");
  $("preview").hidden = true;
}

function showOffer(item = null) {
  currentOffer = item;
  const form = $("offer-form");
  form.reset();
  $("form-title").textContent = item ? "Изменить оффер" : "Добавить оффер";
  feedback($("offer-error"));
  clearPreview();
  $("milestone-inputs").replaceChildren();
  if (item) {
    for (const [key, value] of Object.entries(item)) {
      const field = form.elements.namedItem(key);
      if (!field) continue;
      if (field.type === "checkbox") field.checked = value;
      else field.value = value ?? "";
    }
    for (const step of item.milestones) addStep(step.label, step.quoted);
    if (item.screenshot) {
      $("preview").src = "/api/picture/" + item.id;
      $("preview").hidden = false;
    }
  } else addStep();
  $("offer-dialog").showModal();
}

function showProgress(item, action, step) {
  currentProgress = {id: item.id, action, position: step?.position};
  const form = $("progress-form");
  form.reset();
  feedback($("progress-error"));
  $("progress-title").textContent = progressLabels[action];
  $("progress-step").textContent = step ? item.title + " · " + step.label : item.title;
  $("progress-date").hidden = action !== "start";
  $("progress-amount").hidden = !["confirmed", "withdrawn"].includes(action);
  $("progress-currency").hidden = action !== "withdrawn";
  form.elements.started.value = state.today;
  form.elements.started.max = state.today;
  form.elements.amount.value = step?.reported || step?.quoted || "";
  form.elements.currency.value = item.currency;
  $("progress-dialog").showModal();
}

function fileData(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({mime: file.type, data: String(reader.result).split(",", 2)[1]});
    reader.onerror = () => reject(new Error("Не удалось открыть изображение."));
    reader.readAsDataURL(file);
  });
}

async function action(button, message, fn) {
  button.disabled = true;
  feedback(message);
  try { await fn(); }
  catch (error) { feedback(message, error.message); }
  finally { button.disabled = false; }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => { location.hash = tab.dataset.view; });
});
window.addEventListener("hashchange", () => setView(location.hash.slice(1)));
document.querySelectorAll("[data-close]").forEach((node) => node.addEventListener("click", () => node.closest("dialog").close()));
$("offer-dialog").addEventListener("close", clearPreview);
$("new-offer").addEventListener("click", () => { if (state) showOffer(); });
$("empty-add").addEventListener("click", () => { if (state) showOffer(); });
$("add-step").addEventListener("click", () => addStep());
["search", "platform-filter", "zero-filter", "ready-filter"].forEach((id) => $(id).addEventListener("input", () => { if (state) render(); }));
$("offer-file").addEventListener("change", () => {
  clearPreview();
  const file = $("offer-file").files[0];
  if (file && file.size > 3_000_000) {
    feedback($("offer-error"), "Скриншот должен быть не больше 3 МБ.");
    $("offer-file").value = "";
  } else if (file) {
    previewURL = URL.createObjectURL(file);
    $("preview").src = previewURL;
    $("preview").hidden = false;
  }
});

$("offer-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  void action(event.submitter, $("offer-error"), async () => {
    const data = Object.fromEntries(new FormData(form));
    data.reviewed = form.elements.reviewed.checked;
    if (currentOffer) data.id = currentOffer.id;
    data.milestones = [...$("milestone-inputs").children].map((row) => {
      const [label, amount] = row.querySelectorAll("input");
      return {label: label.value, amount: amount.value};
    });
    const file = $("offer-file").files[0];
    if (file) data.screenshot = await fileData(file);
    const result = await api("/api/offer", data);
    $("offer-dialog").close();
    setView("offers");
    if (location.hash !== "#offers") location.hash = "offers";
    await refresh();
    feedback($("notice"), result.created ? "Оффер сохранён локально." : "Существующая карточка обновлена.");
  });
});

$("progress-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  void action(event.submitter, $("progress-error"), async () => {
    const {id, action: target, position} = currentProgress;
    if (target === "start") {
      await api("/api/start", {id, started: form.elements.started.value, attested: form.elements.attested.checked});
    } else {
      await api("/api/progress", {id, position, state: target, attested: form.elements.attested.checked,
        amount: form.elements.amount.value, currency: form.elements.currency.value});
    }
    $("progress-dialog").close();
    await refresh();
    feedback($("notice"), "Прогресс записан локально.");
  });
});

$("import-json").addEventListener("change", async (event) => {
  const input = event.currentTarget;
  const file = input.files[0];
  if (!file) return;
  await action(input, $("notice"), async () => {
    if (file.size > 4_300_000) throw new Error("Файл слишком большой.");
    const data = JSON.parse(await file.text());
    const {results} = await api("/api/import", Array.isArray(data) ? {offers: data} : data);
    await refresh();
    const errors = results.filter((row) => row.error).map((row) => "Запись " + row.row + " · " + row.error);
    feedback($("notice"), ["Обработано карточек: " + (results.length - errors.length) + ".", ...errors].join("\n"));
  });
  input.value = "";
});

$("send-telegram").addEventListener("click", (event) => {
  void action(event.currentTarget, $("notice"), async () => {
    feedback($("notice"), "Проверяю ближайшие дедлайны…");
    const result = await api("/api/telegram", {});
    feedback($("notice"), result.message);
  });
});

$("check-sources").addEventListener("click", (event) => {
  void action(event.currentTarget, $("notice"), async () => {
    feedback($("notice"), "Проверяю доступ к официальным справкам…");
    const result = await api("/api/sources", {});
    sourceChecks = result.checks;
    feedback($("notice"), "Проверка справок завершена.");
    render();
  });
});

async function init() {
  try {
    state = await api("/api/state");
    const platforms = Object.entries(state.platforms).map(([key, platform]) => [key, platform.name]);
    options($("platform-filter"), platforms);
    options($("offer-form").elements.platform, platforms);
    document.querySelectorAll("[data-choice]").forEach((select) => options(select, Object.entries(labels)));
    document.querySelectorAll("[data-currency]").forEach((select) => options(select,
      currencies.map((c) => [c, c === "POINTS" ? "Баллы" : c])));
    setView(location.hash.slice(1));
  } catch (error) {
    feedback($("notice"), error.message);
  }
}

void init();
