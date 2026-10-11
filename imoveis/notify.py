"""Envio de alertas. Configure por variáveis de ambiente (GitHub Secrets):

- Telegram: TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
- E-mail:   SMTP_USER, SMTP_PASSWORD, EMAIL_TO (opcional SMTP_HOST, SMTP_PORT; padrão Gmail)
- ntfy.sh:  NTFY_TOPIC (app gratuito de push no celular, sem cadastro)
- WhatsApp: CALLMEBOT_PHONE e CALLMEBOT_APIKEY (via callmebot.com). Para vários números,
            separe por vírgula nas duas variáveis, na mesma ordem.
"""
from __future__ import annotations

import base64
import html
import os
import smtplib
import sys
from email.mime.text import MIMEText

from .common import Listing
from .http import Http

SOURCE_NAMES = {"zap": "ZAP Imóveis", "vivareal": "Viva Real", "quintoandar": "QuintoAndar", "zuk": "Portal Zuk"}
BUSINESS_NAMES = {"venda": "Venda", "aluguel": "Aluguel", "leilao": "Leilão"}


def brl(v: float | None) -> str:
    if v is None:
        return "?"
    return "R$ " + f"{v:,.0f}".replace(",", ".")


def describe(l: Listing, note: str = "") -> tuple[str, str]:
    """Devolve (texto_simples, html) de um anúncio."""
    parts = [f"{BUSINESS_NAMES.get(l.business, l.business)} · {SOURCE_NAMES.get(l.source, l.source)}"]
    line2 = [l.neighborhood]
    if l.area:
        line2.append(f"{l.area:.0f} m²")
    if l.bedrooms:
        line2.append(f"{l.bedrooms} quarto{'s' if l.bedrooms > 1 else ''}")
    if l.parking:
        line2.append(f"{l.parking} vaga{'s' if l.parking > 1 else ''}")
    price_parts = []
    if l.business == "aluguel":
        if l.price:
            price_parts.append(f"Aluguel {brl(l.price)}")
        if l.total_monthly:
            price_parts.append(f"total {brl(l.total_monthly)}/mês")
    elif l.business == "leilao":
        price_parts.append(f"Lance mínimo {brl(l.price)}" if l.price else "Lance: ver anúncio")
    else:
        price_parts.append(brl(l.price))
        if l.price and l.area:
            price_parts.append(f"{brl(l.price / l.area)}/m²")
        if l.condo:
            price_parts.append(f"cond. {brl(l.condo)}")
    lines = [" · ".join(parts), " · ".join(line2), " · ".join(price_parts)]
    if l.address:
        lines.insert(1, l.address)
    if l.extra:
        lines.append(l.extra)
    if note:
        lines.insert(0, note)
    text = "\n".join(lines) + f"\n{l.url}"
    head = html.escape(lines[0])
    body = "\n".join(html.escape(x) for x in lines[1:])
    rich = f"<b>{head}</b>\n{body}\n<a href=\"{html.escape(l.url)}\">Abrir anúncio</a>"
    return text, rich


class Notifier:
    def __init__(self, http: Http | None = None):
        self.http = http or Http(0, 0)
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
        self.ntfy = os.environ.get("NTFY_TOPIC")
        phones = [p.strip() for p in os.environ.get("CALLMEBOT_PHONE", "").split(",") if p.strip()]
        keys = [k.strip() for k in os.environ.get("CALLMEBOT_APIKEY", "").split(",") if k.strip()]
        self.whatsapp = list(zip(phones, keys))
        self.smtp_user = os.environ.get("SMTP_USER")
        self.smtp_pass = os.environ.get("SMTP_PASSWORD")
        self.email_to = os.environ.get("EMAIL_TO")
        self.smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "465"))

    @property
    def configured(self) -> bool:
        return bool(
            (self.tg_token and self.tg_chat) or self.ntfy or self.whatsapp
            or (self.smtp_user and self.email_to)
        )

    def send(self, subject: str, items: list[tuple[str, str]], footer: str = "") -> None:
        """items: lista de (texto, html) — cada um vira uma mensagem/bloco."""
        if not self.configured:
            print("[aviso] nenhum canal de notificação configurado; imprimindo:", file=sys.stderr)
            print(subject)
            for t, _ in items:
                print("---\n" + t)
            return
        if self.whatsapp:
            self._whatsapp(subject, items, footer)
        if self.tg_token and self.tg_chat:
            self._telegram(subject, items, footer)
        if self.ntfy:
            self._ntfy(subject, items, footer)
        if self.smtp_user and self.email_to:
            self._email(subject, items, footer)

    def _telegram(self, subject, items, footer):
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        msgs, cur = [], f"<b>{html.escape(subject)}</b>"
        for _, rich in items:
            if len(cur) + len(rich) > 3800:
                msgs.append(cur)
                cur = ""
            cur += "\n\n" + rich
        if footer:
            cur += "\n\n" + html.escape(footer)
        msgs.append(cur)
        for m in msgs:
            r = self.http.post(url, json={
                "chat_id": self.tg_chat, "text": m.strip(), "parse_mode": "HTML",
                "disable_web_page_preview": len(items) != 1,
            })
            if r.status_code != 200:
                print(f"[erro] Telegram: {r.status_code} {r.text[:300]}", file=sys.stderr)

    def _whatsapp(self, subject, items, footer):
        """CallMeBot: GET simples; limita o tamanho e espaça as mensagens para não ser bloqueado."""
        msgs, cur, sent = [], f"*{subject}*", 0
        for text, _ in items:
            if len(cur) + len(text) > 1500:
                if len(msgs) == 4:  # evita enxurrada: até 5 mensagens + 1 aviso
                    msgs.append(cur)
                    cur = None
                    break
                msgs.append(cur)
                cur = ""
            cur += "\n\n" + text
            sent += 1
        if cur is None:
            msgs.append(f"... e mais {len(items) - sent} anúncio(s). "
                        "Veja a lista completa em ANUNCIOS.md no repositório.")
        else:
            if footer:
                cur += "\n\n" + footer
            msgs.append(cur.strip())
        http = Http(3, 5)
        for phone, key in self.whatsapp:
            for m in msgs:
                try:
                    r = http.get("https://api.callmebot.com/whatsapp.php",
                                 params={"phone": phone, "text": m, "apikey": key}, retries=2)
                    if r.status_code != 200 or "error" in r.text.lower()[:500]:
                        print(f"[erro] WhatsApp/CallMeBot: {r.status_code} {r.text[:300]}", file=sys.stderr)
                except Exception as e:
                    print(f"[erro] WhatsApp/CallMeBot: {e}", file=sys.stderr)

    def _ntfy(self, subject, items, footer):
        server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
        title = "=?UTF-8?B?" + base64.b64encode(subject.encode()).decode() + "?="
        for text, _ in (items or [(footer or subject, "")])[:20]:
            headers = {"Title": title, "Tags": "house"}
            last = text.rsplit("\n", 1)[-1]
            if last.startswith("http"):
                headers["Click"] = last
            r = self.http.post(f"{server}/{self.ntfy}", data=text.encode(), headers=headers)
            if r.status_code >= 300:
                print(f"[erro] ntfy: {r.status_code} {r.text[:300]}", file=sys.stderr)
        if len(items) > 20:
            self.http.post(f"{server}/{self.ntfy}", data=f"... e mais {len(items) - 20} anúncios".encode(),
                           headers={"Title": title})

    def _email(self, subject, items, footer):
        blocks = "".join(
            "<p style='margin:0 0 16px'>" + rich.replace("\n", "<br>") + "</p>" for _, rich in items
        )
        body = f"<html><body style='font-family:sans-serif'>{blocks}<p style='color:#888'>{html.escape(footer)}</p></body></html>"
        msg = MIMEText(body, "html", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.smtp_user
        msg["To"] = self.email_to
        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30) as s:
                s.login(self.smtp_user, self.smtp_pass)
                s.sendmail(self.smtp_user, [a.strip() for a in self.email_to.split(",")], msg.as_string())
        except Exception as e:
            print(f"[erro] e-mail: {e}", file=sys.stderr)
