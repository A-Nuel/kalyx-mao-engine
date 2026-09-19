# CollateralVault — deploy & test runbook

This has to run on your machine. Claude's sandbox cannot reach any
blockchain RPC (its network egress is allowlisted to package registries
only), and should not hold your private key. Every step below is copy-paste;
report back errors/tx hashes and they'll get fixed or wired into the repo.

## 0. Install Foundry (one-time, ~1 minute)

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

## 1. Install dependencies

From the repo root:

```bash
forge install foundry-rs/forge-std --no-commit
```

## 2. Run the test suite — DO THIS FIRST, before touching any real chain

```bash
forge test -vv
```

Expected: all tests in `contracts/test/CollateralVault.t.sol` pass. If any
fail, stop here and send the output back — nothing below is safe to run
until this is green.

## 3. Testnet rehearsal (mock token, zero financial risk)

### 3a. Get testnet ETH for gas

Robinhood Chain testnet (chain ID 46630) has no first-party faucet. Use:
https://faucet.quicknode.com/robinhood/testnet

Paste your wallet address there. Drips are small — request early.

### 3b. Set environment variables

```bash
export PRIVATE_KEY=0x...   # your testnet-only key — NEVER your real mainnet key
```

If you don't already have a throwaway testnet wallet, generate one:

```bash
cast wallet new
```

Use that key's address for the faucet in step 3a, not your real wallet.

### 3c. Deploy MockCredit + CollateralVault to testnet

```bash
forge script contracts/script/Deploy.s.sol:DeployTestnet \
  --rpc-url robinhood_testnet \
  --broadcast \
  --private-key $PRIVATE_KEY
```

This prints two addresses: `MockCredit deployed at: 0x...` and
`CollateralVault deployed at: 0x...`. Save both.

### 3d. Rehearse the full cycle manually with `cast`

Replace `$MOCK_CREDIT`, `$VAULT`, `$DEPLOYER` with the addresses/key from 3c.
`POSITION_ID` can be any 32-byte value — using a hash of a string is easiest:

```bash
export MOCK_CREDIT=0x...
export VAULT=0x...
export POSITION_ID=$(cast keccak "demo-position-1")

# Approve the vault to pull 20 mCREDIT (6 decimals: 20_000000)
cast send $MOCK_CREDIT "approve(address,uint256)" $VAULT 20000000 \
  --rpc-url robinhood_testnet --private-key $PRIVATE_KEY

# Lock it
cast send $VAULT "lock(bytes32,uint256)" $POSITION_ID 20000000 \
  --rpc-url robinhood_testnet --private-key $PRIVATE_KEY

# Check the position
cast call $VAULT "getPosition(bytes32)(address,uint256,uint8)" $POSITION_ID \
  --rpc-url robinhood_testnet
# state 1 == LOCKED

# Release it back to yourself (as owner)
cast send $VAULT "release(bytes32)" $POSITION_ID \
  --rpc-url robinhood_testnet --private-key $PRIVATE_KEY

# Confirm state 2 == RELEASED
cast call $VAULT "getPosition(bytes32)(address,uint256,uint8)" $POSITION_ID \
  --rpc-url robinhood_testnet
```

Try a second position and call `forfeit(bytes32,address)` instead of
`release`, sending it to a different address, to rehearse the failure path
too. View both transactions on
https://explorer.testnet.chain.robinhood.com before moving on.

**Do not proceed to step 4 until 3d works cleanly with no reverts you didn't
expect.**

## 4. Mainnet — real CREDIT, small amount only

Use your real wallet (the one holding the $100 CREDIT balance) for this
step. Start small — a few CREDIT, not the whole balance — for the first
real run.

```bash
export PRIVATE_KEY=0x...        # your REAL wallet's key — keep this out of
                                 # shell history / any file that gets committed
export REAL_CREDIT=0xe33322Da1380e61E5AE5DFb21e7F62924c73004C
```

### 4a. Deploy the vault (does not move any CREDIT yet)

```bash
forge script contracts/script/Deploy.s.sol:DeployMainnet \
  --rpc-url robinhood_mainnet \
  --broadcast \
  --private-key $PRIVATE_KEY
```

Save the printed `CollateralVault (mainnet, real CREDIT) deployed at: 0x...`.

### 4b. Lock a small real amount (e.g. 5 CREDIT = 5000000 units)

```bash
export VAULT=0x...  # from 4a
export POSITION_ID=$(cast keccak "kalyx-demo-mainnet-1")

cast send $REAL_CREDIT "approve(address,uint256)" $VAULT 5000000 \
  --rpc-url robinhood_mainnet --private-key $PRIVATE_KEY

cast send $VAULT "lock(bytes32,uint256)" $POSITION_ID 5000000 \
  --rpc-url robinhood_mainnet --private-key $PRIVATE_KEY
```

### 4c. Release it back

```bash
cast send $VAULT "release(bytes32)" $POSITION_ID \
  --rpc-url robinhood_mainnet --private-key $PRIVATE_KEY
```

### 4d. Capture evidence for the pitch/README

From https://robinhoodchain.blockscout.com, grab the explorer links for:
- the vault deployment tx
- the `lock` tx
- the `release` (or `forfeit`) tx

Send those three tx hashes/links back — they get embedded in the README
and demo output as real, checkable evidence instead of simulated references.

## Safety notes

- Never paste a real private key into chat, a committed file, or `.env`
  tracked by git. `contracts/.env` should be in `.gitignore` (already added).
- The vault owner key (whoever deploys it) can call `release`/`forfeit` for
  any position — that's a real, documented limitation for a hackathon
  timeline, not a hidden flaw. See the contract's NatSpec comment above
  `transferOwnership`.
- Test with a small amount before committing more of the $100 balance.
