// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "./interfaces/IERC20.sol";

/// @title CollateralVault
/// @notice Minimal escrow vault for Orbio CREDIT (or any CREDIT-interface-
///         compatible ERC-20) pledged as collateral behind an off-chain
///         Kalyx obligation (a B2B work order, verified by Kalyx's
///         independent auditor).
///
/// @dev Design intent, matching src/domain/collateral.py's state machine:
///
///   1. `lock`    — pledging org transfers `amount` CREDIT into the vault.
///                  Requires prior ERC-20 `approve(vault, amount)`.
///   2. `release` — owner (Kalyx backend signer) returns the full amount
///                  to the original pledger. Called on VERIFIED_SUCCESS.
///   3. `forfeit` — owner sends the full amount to a beneficiary address
///                  instead. Called on VERIFIED_FAILURE.
///
/// This contract is intentionally minimal for a hackathon-timeline demo:
/// - Ownership is a single signer (the Kalyx backend's key), not a DAO or
///   multisig. That is a known, explicitly-documented limitation — see
///   README "Known limitations" — not an oversight. Production hardening
///   (multisig owner, timelock, per-org withdrawal limits) is future work.
/// - It holds exactly one ERC-20 token, set at deploy time.
/// - Positions are identified by a bytes32 `positionId` chosen off-chain by
///   Kalyx (kept equal to the `position_id` in CreditCollateralPosition),
///   NOT by org address alone, so multiple concurrent positions per org are
///   possible and cannot collide.
/// - The contract never interprets "success" or "failure" itself — Kalyx's
///   independent auditor decides, off-chain, and the owner key merely
///   executes that decision on-chain. This mirrors invariant 4 in
///   src/domain/collateral.py: settlement is evidence-driven, not
///   self-reported by whichever party benefits from the outcome.
contract CollateralVault {
    error NotOwner();
    error ZeroAddress();
    error ZeroAmount();
    error PositionAlreadyExists();
    error PositionNotFound();
    error PositionNotLocked();

    enum PositionState {
        NONE,
        LOCKED,
        RELEASED,
        FORFEITED
    }

    struct Position {
        address pledger;
        uint256 amount;
        PositionState state;
    }

    IERC20 public immutable creditToken;
    address public owner;

    mapping(bytes32 => Position) public positions;

    event Locked(bytes32 indexed positionId, address indexed pledger, uint256 amount);
    event Released(bytes32 indexed positionId, address indexed pledger, uint256 amount);
    event Forfeited(bytes32 indexed positionId, address indexed pledger, address indexed beneficiary, uint256 amount);
    event OwnerChanged(address indexed previousOwner, address indexed newOwner);

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address creditTokenAddress, address initialOwner) {
        if (creditTokenAddress == address(0) || initialOwner == address(0)) revert ZeroAddress();
        creditToken = IERC20(creditTokenAddress);
        owner = initialOwner;
    }

    /// @notice Transfer ownership (the Kalyx backend signer key) to a new address.
    /// @dev Single-step transfer, no two-step handshake — matches the
    ///      "minimal for demo" scope noted above. A lost or compromised
    ///      owner key can neither mint nor steal collateral outright
    ///      (release/forfeit only ever pay the pledger or the pre-declared
    ///      beneficiary of that specific position), but it can trigger a
    ///      wrong-party payout, so protect this key exactly as you would
    ///      an exchange hot wallet.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnerChanged(owner, newOwner);
        owner = newOwner;
    }

    /// @notice Lock `amount` of CREDIT from `msg.sender` into a new position.
    /// @dev Caller must have called `creditToken.approve(vault, amount)` first.
    ///      Reverts if `positionId` is already in use — positions are
    ///      write-once until settled, matching the domain model's
    ///      PROPOSED/AUTHORIZED/LOCKED sequence collapsing to a single
    ///      on-chain call once Kalyx has already authorized off-chain.
    function lock(bytes32 positionId, uint256 amount) external {
        if (amount == 0) revert ZeroAmount();
        if (positions[positionId].state != PositionState.NONE) revert PositionAlreadyExists();

        positions[positionId] = Position({pledger: msg.sender, amount: amount, state: PositionState.LOCKED});

        bool ok = creditToken.transferFrom(msg.sender, address(this), amount);
        require(ok, "CollateralVault: transferFrom failed");

        emit Locked(positionId, msg.sender, amount);
    }

    /// @notice Return a locked position's full amount to its original pledger.
    /// @dev Only callable once per position — state moves LOCKED -> RELEASED
    ///      and any further call reverts with PositionNotLocked, which is
    ///      the on-chain mirror of invariant 6 (replay of release/forfeit
    ///      against an already-settled position is impossible).
    function release(bytes32 positionId) external onlyOwner {
        Position storage pos = positions[positionId];
        if (pos.state == PositionState.NONE) revert PositionNotFound();
        if (pos.state != PositionState.LOCKED) revert PositionNotLocked();

        pos.state = PositionState.RELEASED;
        uint256 amount = pos.amount;
        address pledger = pos.pledger;

        bool ok = creditToken.transfer(pledger, amount);
        require(ok, "CollateralVault: release transfer failed");

        emit Released(positionId, pledger, amount);
    }

    /// @notice Pay a locked position's full amount to `beneficiary` instead
    ///         of returning it to the pledger.
    /// @dev Same one-shot state transition and replay protection as
    ///      `release`. `beneficiary` is passed at call time (not stored at
    ///      lock time) so Kalyx can route forfeiture proceeds according to
    ///      its own governed settlement logic (e.g. the counterparty org's
    ///      wallet, or a treasury address) — the vault itself has no
    ///      opinion on where forfeited collateral should go.
    function forfeit(bytes32 positionId, address beneficiary) external onlyOwner {
        if (beneficiary == address(0)) revert ZeroAddress();
        Position storage pos = positions[positionId];
        if (pos.state == PositionState.NONE) revert PositionNotFound();
        if (pos.state != PositionState.LOCKED) revert PositionNotLocked();

        pos.state = PositionState.FORFEITED;
        uint256 amount = pos.amount;
        address pledger = pos.pledger;

        bool ok = creditToken.transfer(beneficiary, amount);
        require(ok, "CollateralVault: forfeit transfer failed");

        emit Forfeited(positionId, pledger, beneficiary, amount);
    }

    /// @notice Read-only helper for off-chain code / tests.
    function getPosition(bytes32 positionId)
        external
        view
        returns (address pledger, uint256 amount, PositionState state)
    {
        Position storage pos = positions[positionId];
        return (pos.pledger, pos.amount, pos.state);
    }
}
