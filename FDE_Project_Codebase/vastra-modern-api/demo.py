# Read environment variables so the API URL can be overridden without editing the script.
import os

# Send the demonstration requests to the running Vastra API.
import httpx


# Run the end-to-end pricing and idempotent-checkout demonstration.
def main() -> None:
    # Default to the local API, while allowing another test deployment to be selected.
    base = os.getenv("VASTRA_API_URL", "http://localhost:8200")

    # Remind the operator that checkout also depends on the local partner service.
    print("Start the partners on :8100 and Vastra API on :8200 before running this script.")

    # Reuse one HTTP client for the price request and both checkout attempts.
    with httpx.Client(base_url=base, timeout=10) as client:
        # Identify this caller as the web channel and reuse one key to test retry behavior.
        headers = {"X-API-Key": "vastra-web", "Idempotency-Key": "demo-checkout-001"}

        # Request a price with coupon and member discounts, using the same API as checkout.
        price = client.get("/v1/prices/SKU00003", params={"coupon_pct": 10, "member": "true"}, headers={"X-API-Key": "vastra-web"})

        # Place an order with the idempotency key so the service reserves stock and charges once.
        checkout = client.post(
            "/v1/checkout",
            json={"sku": "SKU00003", "qty": 1, "coupon_pct": 10, "member": True, "pincode": "560001"},
            headers=headers,
        )

        # Repeat the identical request and key to confirm the API returns the original order.
        retry = client.post(
            "/v1/checkout",
            json={"sku": "SKU00003", "qty": 1, "coupon_pct": 10, "member": True, "pincode": "560001"},
            headers=headers,
        )

        # Print the HTTP status and decoded JSON so each response can be inspected.
        print("price:", price.status_code, price.json())
        print("checkout:", checkout.status_code, checkout.json())
        print("retry:", retry.status_code, retry.json())


# Run the demonstration only when this file is executed directly, not when imported.
if __name__ == "__main__":
    main()
