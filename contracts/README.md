# Phase 18 — CREDIT CollateralVault

## What this contract adds

Orbio CREDIT is a transferable ERC-20. Kalyx adds a governed collateral
primitive on top of that transferability:

`LOCKED -> RELEASED` on independently verified success, or
`LOCKED -> FORFEITED` on independently verified failure.

The vault does **not** claim that Orbio natively supports collateral. The
beneficiary is bound when a position is locked and cannot be redirected later.

## 1. Verify the Solidity contract first

Install Foundry:

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

From the repository root:

```bash
forge install foundry-rs/forge-std --no-commit
forge test -vv
```

Do not deploy real CREDIT unless this suite is green.

GitHub CI also runs `forge test -vv` whenever `contracts/**` changes.

## 2. Testnet rehearsal

Robinhood Chain testnet is chain ID 46630. Use a throwaway wallet for gas and
rehearsal.

```bash
export PRIVATE_KEY=0x...
forge script contracts/script/Deploy.s.sol:DeployTestnet \
  --rpc-url robinhood_testnet --broadcast --private-key $PRIVATE_KEY
```

Save the printed MockCredit and CollateralVault addresses.

Approve and lock:

```bash
export MOCK_CREDIT=0x...
export VAULT=0x...
export POSITION_ID=$(cast keccak "kalyx-demo-testnet-1")
export BENEFICIARY=0x...

cast send $MOCK_CREDIT "approve(address,uint256)" $VAULT 20000000 \
  --rpc-url robinhood_testnet --private-key $PRIVATE_KEY

cast send $VAULT "lock(bytes32,uint256,address)" $POSITION_ID 20000000 $BENEFICIARY \
  --rpc-url robinhood_testnet --private-key $PRIVATE_KEY

cast call $VAULT "getPosition(bytes32)(address,address,uint256,uint8)" $POSITION_ID \
  --rpc-url robinhood_testnet
# state 1 = LOCKED
```

Success path:

```bash
cast send $VAULT "release(bytes32)" $POSITION_ID \
  --rpc-url robinhood_testnet --private-key $PRIVATE_KEY
```

Failure path on a fresh position:

```bash
export POSITION_ID=$(cast keccak "kalyx-demo-testnet-forfeit")
cast send $MOCK_CREDIT "approve(address,uint256)" $VAULT 20000000 \
  --rpc-url robinhood_testnet --private-key $PRIVATE_KEY
cast send $VAULT "lock(bytes32,uint256,address)" $POSITION_ID 20000000 $BENEFICIARY \
  --rpc-url robinhood_testnet --private-key $PRIVATE_KEY
cast send $VAULT "forfeit(bytes32)" $POSITION_ID \
  --rpc-url robinhood_testnet --private-key $PRIVATE_KEY
```

## 3. Real CREDIT deployment

The deployed CREDIT address documented by Orbio is:

`0xe33322Da1380e61E5AE5DFb21e7F62924c73004C` on Robinhood Chain mainnet
(chain ID 4663).

Deploy the vault without moving any CREDIT:

```bash
export PRIVATE_KEY=0x...
forge script contracts/script/Deploy.s.sol:DeployMainnet \
  --rpc-url robinhood_mainnet --broadcast --private-key $PRIVATE_KEY
```

For the first real run, use a few CREDIT only. Never put a private key in the
repository or in chat.

The first live Kalyx lock requires two signing roles:

- **pledger key** — owns CREDIT and signs `approve()` + `lock()`
- **vault owner key** — signs `release()` or `forfeit()`

For a one-wallet hackathon rehearsal these can be the same wallet. The Python
adapter still keeps the roles explicit so the multi-organisation model does
not silently use the settlement signer as the pledger.

## Known hackathon limitation

The vault owner is a single signer. Production hardening should move that
authority behind the existing Kalyx multi-signature governance model or a
dedicated contract-controlled policy account. This prototype deliberately
keeps the Solidity surface small enough to verify before the deadline.

## Evidence to capture

For a real demonstration, retain:

1. vault deployment transaction
2. CREDIT approval transaction
3. CREDIT lock transaction
4. release or forfeit transaction

Those hashes are the proof that the CREDIT collateral path was actually
on-chain. Never label simulated references as live transaction hashes.
