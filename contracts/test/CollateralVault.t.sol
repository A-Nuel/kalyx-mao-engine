// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {CollateralVault} from "../CollateralVault.sol";
import {MockCredit} from "../mocks/MockCredit.sol";

contract CollateralVaultTest is Test {
    CollateralVault vault;
    MockCredit credit;

    address owner = address(0xA11CE);
    address pledger = address(0xB0B);
    address beneficiary = address(0xCAFE);
    address attacker = address(0xEVE);

    bytes32 constant POS_1 = keccak256("position-1");
    bytes32 constant POS_2 = keccak256("position-2");

    function setUp() public {
        credit = new MockCredit();
        vault = new CollateralVault(address(credit), owner);

        credit.mint(pledger, 1_000_000_000); // 1,000 mCREDIT at 6 decimals
        vm.prank(pledger);
        credit.approve(address(vault), type(uint256).max);
    }

    // ---- lock() ----

    function test_lock_transfers_tokens_into_vault() public {
        uint256 amount = 20_000_000; // 20 mCREDIT

        vm.prank(pledger);
        vault.lock(POS_1, amount);

        assertEq(credit.balanceOf(address(vault)), amount);
        assertEq(credit.balanceOf(pledger), 1_000_000_000 - amount);

        (address storedPledger, uint256 storedAmount, CollateralVault.PositionState state) =
            vault.getPosition(POS_1);
        assertEq(storedPledger, pledger);
        assertEq(storedAmount, amount);
        assertEq(uint8(state), uint8(CollateralVault.PositionState.LOCKED));
    }

    function test_lock_reverts_on_zero_amount() public {
        vm.prank(pledger);
        vm.expectRevert(CollateralVault.ZeroAmount.selector);
        vault.lock(POS_1, 0);
    }

    function test_lock_reverts_without_approval() public {
        address unapproved = address(0xD00D);
        credit.mint(unapproved, 100);
        vm.prank(unapproved);
        vm.expectRevert(); // ERC20 allowance underflow inside transferFrom
        vault.lock(POS_1, 50);
    }

    function test_lock_reverts_on_duplicate_position_id() public {
        vm.startPrank(pledger);
        vault.lock(POS_1, 10_000_000);
        vm.expectRevert(CollateralVault.PositionAlreadyExists.selector);
        vault.lock(POS_1, 5_000_000);
        vm.stopPrank();
    }

    // ---- release() ----

    function test_release_returns_full_amount_to_pledger() public {
        uint256 amount = 15_000_000;
        vm.prank(pledger);
        vault.lock(POS_1, amount);

        uint256 pledgerBalBefore = credit.balanceOf(pledger);

        vm.prank(owner);
        vault.release(POS_1);

        assertEq(credit.balanceOf(pledger), pledgerBalBefore + amount);
        assertEq(credit.balanceOf(address(vault)), 0);

        (, , CollateralVault.PositionState state) = vault.getPosition(POS_1);
        assertEq(uint8(state), uint8(CollateralVault.PositionState.RELEASED));
    }

    function test_release_reverts_for_non_owner() public {
        vm.prank(pledger);
        vault.lock(POS_1, 10_000_000);

        vm.prank(attacker);
        vm.expectRevert(CollateralVault.NotOwner.selector);
        vault.release(POS_1);
    }

    function test_release_reverts_on_unknown_position() public {
        vm.prank(owner);
        vm.expectRevert(CollateralVault.PositionNotFound.selector);
        vault.release(POS_1);
    }

    /// @dev Mirrors src/domain/collateral.py test_replay_release_after_settlement_is_rejected.
    function test_release_cannot_be_replayed() public {
        vm.prank(pledger);
        vault.lock(POS_1, 10_000_000);

        vm.startPrank(owner);
        vault.release(POS_1);
        vm.expectRevert(CollateralVault.PositionNotLocked.selector);
        vault.release(POS_1); // replay attempt
        vm.stopPrank();
    }

    // ---- forfeit() ----

    function test_forfeit_pays_beneficiary_not_pledger() public {
        uint256 amount = 25_000_000;
        vm.prank(pledger);
        vault.lock(POS_1, amount);

        uint256 pledgerBalBefore = credit.balanceOf(pledger);

        vm.prank(owner);
        vault.forfeit(POS_1, beneficiary);

        assertEq(credit.balanceOf(beneficiary), amount);
        assertEq(credit.balanceOf(pledger), pledgerBalBefore); // pledger gets nothing back
        assertEq(credit.balanceOf(address(vault)), 0);

        (, , CollateralVault.PositionState state) = vault.getPosition(POS_1);
        assertEq(uint8(state), uint8(CollateralVault.PositionState.FORFEITED));
    }

    function test_forfeit_reverts_for_non_owner() public {
        vm.prank(pledger);
        vault.lock(POS_1, 10_000_000);

        vm.prank(attacker);
        vm.expectRevert(CollateralVault.NotOwner.selector);
        vault.forfeit(POS_1, beneficiary);
    }

    function test_forfeit_reverts_on_zero_beneficiary() public {
        vm.prank(pledger);
        vault.lock(POS_1, 10_000_000);

        vm.prank(owner);
        vm.expectRevert(CollateralVault.ZeroAddress.selector);
        vault.forfeit(POS_1, address(0));
    }

    /// @dev Mirrors src/domain/collateral.py test_replay_forfeit_after_forfeit_is_rejected.
    function test_forfeit_cannot_be_replayed() public {
        vm.prank(pledger);
        vault.lock(POS_1, 10_000_000);

        vm.startPrank(owner);
        vault.forfeit(POS_1, beneficiary);
        vm.expectRevert(CollateralVault.PositionNotLocked.selector);
        vault.forfeit(POS_1, beneficiary); // replay attempt
        vm.stopPrank();
    }

    /// @dev Cross-check: once released, forfeit must also be rejected (and
    /// vice versa) — a position settled one way can never be settled the
    /// other way, matching invariant 6 in the Python domain model exactly.
    function test_cannot_forfeit_an_already_released_position() public {
        vm.prank(pledger);
        vault.lock(POS_1, 10_000_000);

        vm.startPrank(owner);
        vault.release(POS_1);
        vm.expectRevert(CollateralVault.PositionNotLocked.selector);
        vault.forfeit(POS_1, beneficiary);
        vm.stopPrank();
    }

    function test_cannot_release_an_already_forfeited_position() public {
        vm.prank(pledger);
        vault.lock(POS_1, 10_000_000);

        vm.startPrank(owner);
        vault.forfeit(POS_1, beneficiary);
        vm.expectRevert(CollateralVault.PositionNotLocked.selector);
        vault.release(POS_1);
        vm.stopPrank();
    }

    // ---- multiple concurrent positions ----

    function test_multiple_positions_do_not_interfere() public {
        vm.startPrank(pledger);
        vault.lock(POS_1, 10_000_000);
        vault.lock(POS_2, 30_000_000);
        vm.stopPrank();

        vm.startPrank(owner);
        vault.release(POS_1);
        vault.forfeit(POS_2, beneficiary);
        vm.stopPrank();

        (, , CollateralVault.PositionState state1) = vault.getPosition(POS_1);
        (, , CollateralVault.PositionState state2) = vault.getPosition(POS_2);
        assertEq(uint8(state1), uint8(CollateralVault.PositionState.RELEASED));
        assertEq(uint8(state2), uint8(CollateralVault.PositionState.FORFEITED));
    }

    // ---- ownership ----

    function test_transferOwnership_moves_control() public {
        address newOwner = address(0xF00D);

        vm.prank(owner);
        vault.transferOwnership(newOwner);

        assertEq(vault.owner(), newOwner);

        vm.prank(pledger);
        vault.lock(POS_1, 10_000_000);

        // Old owner can no longer act.
        vm.prank(owner);
        vm.expectRevert(CollateralVault.NotOwner.selector);
        vault.release(POS_1);

        // New owner can.
        vm.prank(newOwner);
        vault.release(POS_1);
    }

    function test_transferOwnership_reverts_for_non_owner() public {
        vm.prank(attacker);
        vm.expectRevert(CollateralVault.NotOwner.selector);
        vault.transferOwnership(attacker);
    }

    // ---- constructor guards ----

    function test_constructor_reverts_on_zero_addresses() public {
        vm.expectRevert(CollateralVault.ZeroAddress.selector);
        new CollateralVault(address(0), owner);

        vm.expectRevert(CollateralVault.ZeroAddress.selector);
        new CollateralVault(address(credit), address(0));
    }
}
