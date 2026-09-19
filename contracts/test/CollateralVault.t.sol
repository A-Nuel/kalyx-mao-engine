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

        credit.mint(pledger, 1_000_000_000);
        vm.prank(pledger);
        credit.approve(address(vault), type(uint256).max);
    }

    function _lock(bytes32 positionId, uint256 amount) internal {
        vm.prank(pledger);
        vault.lock(positionId, amount, beneficiary);
    }

    function test_lock_transfers_tokens_and_binds_beneficiary() public {
        uint256 amount = 20_000_000;
        _lock(POS_1, amount);

        assertEq(credit.balanceOf(address(vault)), amount);
        assertEq(credit.balanceOf(pledger), 1_000_000_000 - amount);

        (address storedPledger, address storedBeneficiary, uint256 storedAmount, CollateralVault.PositionState state) =
            vault.getPosition(POS_1);
        assertEq(storedPledger, pledger);
        assertEq(storedBeneficiary, beneficiary);
        assertEq(storedAmount, amount);
        assertEq(uint8(state), uint8(CollateralVault.PositionState.LOCKED));
    }

    function test_lock_reverts_on_zero_amount() public {
        vm.prank(pledger);
        vm.expectRevert(CollateralVault.ZeroAmount.selector);
        vault.lock(POS_1, 0, beneficiary);
    }

    function test_lock_reverts_on_zero_beneficiary() public {
        vm.prank(pledger);
        vm.expectRevert(CollateralVault.ZeroAddress.selector);
        vault.lock(POS_1, 10_000_000, address(0));
    }

    function test_lock_reverts_without_approval() public {
        address unapproved = address(0xD00D);
        credit.mint(unapproved, 100);
        vm.prank(unapproved);
        vm.expectRevert();
        vault.lock(POS_1, 50, beneficiary);
    }

    function test_lock_reverts_on_duplicate_position_id() public {
        _lock(POS_1, 10_000_000);
        vm.prank(pledger);
        vm.expectRevert(CollateralVault.PositionAlreadyExists.selector);
        vault.lock(POS_1, 5_000_000, beneficiary);
    }

    function test_release_returns_full_amount_to_pledger() public {
        uint256 amount = 15_000_000;
        _lock(POS_1, amount);
        uint256 beforeBal = credit.balanceOf(pledger);

        vm.prank(owner);
        vault.release(POS_1);

        assertEq(credit.balanceOf(pledger), beforeBal + amount);
        assertEq(credit.balanceOf(address(vault)), 0);

        (, , , CollateralVault.PositionState state) = vault.getPosition(POS_1);
        assertEq(uint8(state), uint8(CollateralVault.PositionState.RELEASED));
    }

    function test_release_reverts_for_non_owner() public {
        _lock(POS_1, 10_000_000);
        vm.prank(attacker);
        vm.expectRevert(CollateralVault.NotOwner.selector);
        vault.release(POS_1);
    }

    function test_release_reverts_on_unknown_position() public {
        vm.prank(owner);
        vm.expectRevert(CollateralVault.PositionNotFound.selector);
        vault.release(POS_1);
    }

    function test_release_cannot_be_replayed() public {
        _lock(POS_1, 10_000_000);
        vm.startPrank(owner);
        vault.release(POS_1);
        vm.expectRevert(CollateralVault.PositionNotLocked.selector);
        vault.release(POS_1);
        vm.stopPrank();
    }

    function test_forfeit_pays_bound_beneficiary() public {
        uint256 amount = 25_000_000;
        _lock(POS_1, amount);
        uint256 pledgerBefore = credit.balanceOf(pledger);

        vm.prank(owner);
        vault.forfeit(POS_1);

        assertEq(credit.balanceOf(beneficiary), amount);
        assertEq(credit.balanceOf(pledger), pledgerBefore);
        assertEq(credit.balanceOf(address(vault)), 0);

        (, address storedBeneficiary, , CollateralVault.PositionState state) = vault.getPosition(POS_1);
        assertEq(storedBeneficiary, beneficiary);
        assertEq(uint8(state), uint8(CollateralVault.PositionState.FORFEITED));
    }

    function test_forfeit_reverts_for_non_owner() public {
        _lock(POS_1, 10_000_000);
        vm.prank(attacker);
        vm.expectRevert(CollateralVault.NotOwner.selector);
        vault.forfeit(POS_1);
    }

    function test_forfeit_cannot_redirect_beneficiary() public {
        address attackerBeneficiary = address(0xD00D);
        _lock(POS_1, 10_000_000);

        vm.prank(owner);
        vault.forfeit(POS_1);

        assertEq(credit.balanceOf(beneficiary), 10_000_000);
        assertEq(credit.balanceOf(attackerBeneficiary), 0);
    }

    function test_forfeit_cannot_be_replayed() public {
        _lock(POS_1, 10_000_000);
        vm.startPrank(owner);
        vault.forfeit(POS_1);
        vm.expectRevert(CollateralVault.PositionNotLocked.selector);
        vault.forfeit(POS_1);
        vm.stopPrank();
    }

    function test_cannot_forfeit_after_release() public {
        _lock(POS_1, 10_000_000);
        vm.startPrank(owner);
        vault.release(POS_1);
        vm.expectRevert(CollateralVault.PositionNotLocked.selector);
        vault.forfeit(POS_1);
        vm.stopPrank();
    }

    function test_cannot_release_after_forfeit() public {
        _lock(POS_1, 10_000_000);
        vm.startPrank(owner);
        vault.forfeit(POS_1);
        vm.expectRevert(CollateralVault.PositionNotLocked.selector);
        vault.release(POS_1);
        vm.stopPrank();
    }

    function test_multiple_positions_do_not_interfere() public {
        _lock(POS_1, 10_000_000);
        _lock(POS_2, 30_000_000);

        vm.startPrank(owner);
        vault.release(POS_1);
        vault.forfeit(POS_2);
        vm.stopPrank();

        (, , , CollateralVault.PositionState state1) = vault.getPosition(POS_1);
        (, , , CollateralVault.PositionState state2) = vault.getPosition(POS_2);
        assertEq(uint8(state1), uint8(CollateralVault.PositionState.RELEASED));
        assertEq(uint8(state2), uint8(CollateralVault.PositionState.FORFEITED));
    }

    function test_transferOwnership_moves_control() public {
        address newOwner = address(0xF00D);

        vm.prank(owner);
        vault.transferOwnership(newOwner);
        assertEq(vault.owner(), newOwner);

        _lock(POS_1, 10_000_000);

        vm.prank(owner);
        vm.expectRevert(CollateralVault.NotOwner.selector);
        vault.release(POS_1);

        vm.prank(newOwner);
        vault.release(POS_1);
    }

    function test_transferOwnership_reverts_for_non_owner() public {
        vm.prank(attacker);
        vm.expectRevert(CollateralVault.NotOwner.selector);
        vault.transferOwnership(attacker);
    }

    function test_constructor_reverts_on_zero_addresses() public {
        vm.expectRevert(CollateralVault.ZeroAddress.selector);
        new CollateralVault(address(0), owner);

        vm.expectRevert(CollateralVault.ZeroAddress.selector);
        new CollateralVault(address(credit), address(0));
    }
}
