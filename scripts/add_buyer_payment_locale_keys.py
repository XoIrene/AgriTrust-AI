"""One-off helper: translation keys for the buyer transaction statement.

Idempotent - running it again only fills in keys that are missing.
"""
import json
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "frontend" / "locales"

KEYS = {
    "en": {
        # the transactions section
        "buyer.transactions": "Payment transactions",
        "buyer.transactions_intro": "Every crop purchase you have made, and how you paid for it.",
        "buyer.no_transactions": "You have not made a purchase yet.",
        "buyer.transaction_date": "Date",
        "buyer.transaction_crop": "Crop",
        "buyer.payment_method": "Payment method",
        "buyer.method_group_mobile": "Mobile money",
        "buyer.method_group_lipa": "Lipa number",
        "buyer.method_group_bank": "Bank account",
        "buyer.method_group_checkout": "Online checkout",
        "buyer.choose_method": "How would you like to pay?",
        "buyer.method_mobile_note": "Dial the Lipa number or USSD code from your mobile money app.",
        "buyer.method_lipa_note": "Use Lipa Namba and quote the reference.",
        "buyer.method_bank_note": "Transfer to the account and quote the reference.",
        "buyer.method_checkout_note": "You will be taken to a secure checkout page.",
        "buyer.pay_with": "Pay with",
        "buyer.dial_this": "Dial",
        "buyer.lipa_number_is": "Lipa number",
        "buyer.account_number_is": "Account number",
        "buyer.your_reference": "Your reference",
        "buyer.total_paid": "Total paid",
        "buyer.total_pending": "Awaiting payment",
        "buyer.methods_used": "Payment methods you have used",
        "buyer.filter_status": "Status",
        "buyer.filter_method": "Method",
        "buyer.all_statuses": "All statuses",
        "buyer.all_methods": "All methods",
    },
    "sw": {
        "buyer.transactions": "Miamala ya malipo",
        "buyer.transactions_intro": "Kila ununuzi wa mazao uliofanya, na jinsi ulivyolipa.",
        "buyer.no_transactions": "Bado hujafanya ununuzi wowote.",
        "buyer.transaction_date": "Tarehe",
        "buyer.transaction_crop": "Zao",
        "buyer.payment_method": "Njia ya malipo",
        "buyer.method_group_mobile": "Pesa ya simu",
        "buyer.method_group_lipa": "Namba ya Lipa",
        "buyer.method_group_bank": "Akaunti ya benki",
        "buyer.method_group_checkout": "Malipo mtandaoni",
        "buyer.choose_method": "Ungependa kulipa kwa njia gani?",
        "buyer.method_mobile_note": "Piga namba ya Lipa au msimbo wa USSD kutoka programu yako ya pesa ya simu.",
        "buyer.method_lipa_note": "Tumia Lipa Namba na sema namba ya kumbukumbu.",
        "buyer.method_bank_note": "Hamisha pesa kwenye akaunti na sema namba ya kumbukumbu.",
        "buyer.method_checkout_note": "Utapelekwa kwenye ukurasa salama za malipo.",
        "buyer.pay_with": "Lipa kwa",
        "buyer.dial_this": "Piga",
        "buyer.lipa_number_is": "Namba ya Lipa",
        "buyer.account_number_is": "Namba ya akaunti",
        "buyer.your_reference": "Namba yako ya kumbukumbu",
        "buyer.total_paid": "Jumla iliyolipwa",
        "buyer.total_pending": "Inasubiri malipo",
        "buyer.methods_used": "Njia za malipo ulizotumia",
        "buyer.filter_status": "Hali",
        "buyer.filter_method": "Njia",
        "buyer.all_statuses": "Hali zote",
        "buyer.all_methods": "Njia zote",
    },
}


def main() -> None:
    for lang, entries in KEYS.items():
        path = LOCALES / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        added = [k for k in entries if k not in data]
        data.update(entries)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{lang}: added {len(added)} key(s), {len(data)} total")


if __name__ == "__main__":
    main()
