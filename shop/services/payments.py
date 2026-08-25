import requests

REQUEST_URL = "https://api.zarinpal.com/pg/v4/payment/request.json"
VERIFY_URL = "https://api.zarinpal.com/pg/v4/payment/verify.json"
STARTPAY_URL = "https://www.zarinpal.com/pg/StartPay/"


class PaymentError(RuntimeError):
    pass


def _post(url, payload):
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"User-Agent": "DeltaJanebi/1.0", "Content-Type": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        raise PaymentError(f"ارتباط با زرین‌پال ناموفق بود: {exc}") from exc
    if body.get("errors"):
        err = body["errors"]
        raise PaymentError(f"خطای زرین‌پال {err.get('code', '')}: {err.get('message', 'خطای نامشخص')}")
    return body.get("data") or {}


def request_zarinpal_payment(*, merchant_id, amount_toman, callback_url, description, mobile="", email=""):
    if not merchant_id:
        raise PaymentError("مرچنت زرین‌پال در تنظیمات ثبت نشده است.")
    metadata = {}
    if mobile:
        metadata["mobile"] = mobile
    if email:
        metadata["email"] = email
    data = _post(
        REQUEST_URL,
        {
            "merchant_id": merchant_id.strip(),
            "amount": int(amount_toman) * 10,
            "callback_url": callback_url,
            "description": description[:255],
            "metadata": metadata,
        },
    )
    if int(data.get("code") or 0) != 100 or not data.get("authority"):
        raise PaymentError(f"درخواست پرداخت پذیرفته نشد. کد: {data.get('code', 'نامشخص')}")
    authority = data["authority"]
    return authority, STARTPAY_URL + authority


def verify_zarinpal_payment(*, merchant_id, amount_toman, authority):
    data = _post(
        VERIFY_URL,
        {
            "merchant_id": merchant_id.strip(),
            "amount": int(amount_toman) * 10,
            "authority": authority,
        },
    )
    code = int(data.get("code") or 0)
    if code not in (100, 101):
        raise PaymentError(f"تأیید پرداخت ناموفق بود. کد: {code}")
    return {
        "code": code,
        "ref_id": str(data.get("ref_id") or ""),
        "card_pan": str(data.get("card_pan") or ""),
        "card_hash": str(data.get("card_hash") or ""),
        "fee": int(data.get("fee") or 0),
    }
