// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "./interfaces/IERC20.sol";

/// @title CollateralVault
/// @notice Minimal escrow vault for transferable CREDIT pledged behind a
///         governed Kalyx obligation.
/// @dev Kalyx supplies the governance/audit decision off-chain; this vault
///      only enforces token custody, immutable beneficiary binding, and
///      one-shot settlement. The owner is intentionally a single demo signer.
contract CollateralVault {
    error NotOwner();
    error ZeroAddress();
    error ZeroAmount();
    error PositionAlreadyExists();
    error PositionNotFound();
    error PositionNotLocked();

    enum PositionState { NONE, LOCKED, RELEASED, FORFEITED }

    struct Position {
        address pledger;
        address beneficiary;
        uint256 amount;
        PositionState state;
    }

    IERC20 public immutable creditToken;
    address public owner;
    mapping(bytes32 => Position) public positions;

    event Locked(bytes32 indexed positionId, address indexed pledger, address indexed beneficiary, uint256 amount);
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

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnerChanged(owner, newOwner);
        owner = newOwner;
    }

    /// @notice Lock CREDIT from msg.sender. The beneficiary is immutable for
    ///         the lifetime of the position and is the only destination on
    ///         a governed forfeiture.
    /// @dev The pledger must approve this vault before calling lock().
    function lock(bytes32 positionId, uint256 amount, address beneficiary) external {
        if (amount == 0) revert ZeroAmount();
        if (beneficiary == address(0)) revert ZeroAddress();
        if (positions[positionId].state != PositionState.NONE) revert PositionAlreadyExists();

        // Do the token transfer before committing the position state. A
        // reverting transfer rolls the entire transaction back atomically.
        bool ok = creditToken.transferFrom(msg.sender, address(this), amount);
        require(ok, "CollateralVault: transferFrom failed");

        positions[positionId] = Position({
            pledger: msg.sender,
            beneficiary: beneficiary,
            amount: amount,
            state: PositionState.LOCKED
        });

        emit Locked(positionId, msg.sender, beneficiary, amount);
    }

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

    function forfeit(bytes32 positionId) external onlyOwner {
        Position storage pos = positions[positionId];
        if (pos.state == PositionState.NONE) revert PositionNotFound();
        if (pos.state != PositionState.LOCKED) revert PositionNotLocked();

        pos.state = PositionState.FORFEITED;
        uint256 amount = pos.amount;
        address pledger = pos.pledger;
        address beneficiary = pos.beneficiary;

        bool ok = creditToken.transfer(beneficiary, amount);
        require(ok, "CollateralVault: forfeit transfer failed");
        emit Forfeited(positionId, pledger, beneficiary, amount);
    }

    function getPosition(bytes32 positionId)
        external
        view
        returns (address pledger, address beneficiary, uint256 amount, PositionState state)
    {
        Position storage pos = positions[positionId];
        return (pos.pledger, pos.beneficiary, pos.amount, pos.state);
    }
}
