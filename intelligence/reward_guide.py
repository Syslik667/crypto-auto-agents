"""Manual instructions from known terms. Never invent claim URLs."""


def build(item):
    terms = item["terms"]
    blockers = list(item["exclusions"])
    if not item["official"]:
        blockers.append("Найдите подтверждение акции на официальном сайте проекта")
    if item["risk"]["status"] != "Низкий обнаруженный риск":
        blockers.append("Завершите проверку риска по указанным источникам")
    for field in ("Германия", "Депозит", "Комиссии", "KYC", "Окончание", "Вывод"):
        if terms[field] == "не указано":
            blockers.append("Уточните условие «" + field + "»")
    steps = ["Сверьте название акции, срок и доступность для Германии в официальном анонсе."]
    if item["exclusions"] or item["risk"]["status"] == "Высокий риск":
        steps = ["Пропустите участие по текущим условиям.", *blockers]
    elif blockers:
        steps += blockers
    elif terms["Участие"] == "пройти викторину":
        steps.append("На официальной странице акции прочитайте учебные материалы и пройдите викторину вручную.")
    elif terms["Участие"] == "запись в лист ожидания":
        steps.append("На официальной странице проверьте условия обработки данных и вручную заполните форму ожидания.")
    else:
        steps.append("На официальной странице уточните полный список заданий и разрешённый способ участия.")
    if not blockers:
        steps.append("Проверьте вид награды, дату начисления, минимальную сумму вывода и комиссии.")
        steps.append("После начисления проверьте доступность вывода в интерфейсе проекта. Считайте доход после фактического получения.")
    return {"source": item["url"], "blockers": list(dict.fromkeys(blockers)), "steps": steps,
            "reward": terms["Награда"], "withdrawal": terms["Вывод"]}
