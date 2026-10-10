# INKORA automatic revenue sharing

## Confirmed business requirement

The user requires a 90% owner / 10% INKORA gross split per customer payment.
For ₹10 captured, the gross allocation is ₹9 to the kiosk owner and ₹1 to INKORA.
The intended provider arrangement is Razorpay Route with an onboarded linked
account per owner. Transfer the owner's share after valid capture; retain the
platform share in the INKORA merchant account. An extra transfer to INKORA is not
needed when INKORA is the collecting platform account.

This changes the earlier proposal for manually paying owners later. It does not
mean two simultaneous bank credits: capture, linked-account transfer, transfer
settlement and bank settlement are distinct states controlled by the provider.
Show those states honestly. Never claim an API acknowledgement means a bank
received money.

`inkora/revenue_share.py` defines a deterministic integer-paise allocation.
INKORA's share is rounded half-up to a paisa, with the owner receiving the
remainder, conserving the full amount. For ₹0.25, this is ₹0.03 INKORA / ₹0.22
owner. This rounding must be disclosed in the commercial agreement. Calculation
alone does not initiate a transfer or record confirmed revenue.

## Prerequisites still to verify

Razorpay test mode is available according to the user. Route enablement, linked
account onboarding/capabilities and the provider-approved platform arrangement
remain unverified. Do not invent linked account IDs or start live transfers.
Confirm which party bears provider fees, taxes on those fees, refunds and disputes
before guaranteeing owner net proceeds or INKORA's net revenue. The gross split
is separate from those costs; fee deductions may reduce bank settlement amounts.

## Required integration

- Bind an immutable INR quote, owner, kiosk, linked account and rate snapshot to
  each provider order/payment attempt. Browser input never selects the recipient.
- Verify webhook signatures over exact request bytes, account/order/amount/
  currency/capture status, expiry and test/live mode before authorizing a job.
- Deduplicate provider event IDs and make capture plus ledger/outbox writes
  transactional. Duplicate or out-of-order delivery must not create another print
  or another transfer.
- Use the documented Route transfer mechanism for captured payments. Confirm its
  current retry/idempotency behavior from official documentation before coding
  network retries. A timeout after transfer submission is an uncertain outcome;
  reconcile rather than blindly sending another ₹9.
- Track pending, processed, failed, reversed and settled transfers separately.
  Failed transfer delivery is an owner liability needing review, not a fake
  successful payout. Reconcile provider totals and surface unresolved amounts.
- Refunds/disputes need explicit linked-transfer reversal and append-only ledger
  entries. Preserve audit history; never mutate old captured amounts.
- Only provider-confirmed payments contribute to confirmed revenue. Simulator
  quotes remain simulation value. Use the same INKORA UI palette and strict owner
  scope for transaction, transfer, refund and settlement pages.

Current staging remains a payment-free simulation. No checkout, live charge,
transfer, linked account or database migration is enabled by this contract.
