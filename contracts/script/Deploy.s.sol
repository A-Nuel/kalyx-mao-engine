// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {CollateralVault} from "../CollateralVault.sol";
import {MockCredit} from "../mocks/MockCredit.sol";

/// @notice Deploys MockCredit + CollateralVault together — for TESTNET
/// rehearsal only. Run this first, always, before DeployVaultMainnet.
///
/// Usage (see contracts/README.md for the full runbook):
///   forge script contracts/script/DeployTestnet.s.sol \
///     --rpc-url robinhood_testnet --broadcast --private-key $PRIVATE_KEY
contract DeployTestnet is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        vm.startBroadcast(deployerKey);

        MockCredit mockCredit = new MockCredit();
        CollateralVault vault = new CollateralVault(address(mockCredit), deployer);

        // Mint the deployer some mock CREDIT to rehearse lock/release/forfeit with.
        mockCredit.mint(deployer, 1_000_000_000); // 1,000 mCREDIT

        vm.stopBroadcast();

        console.log("MockCredit deployed at:", address(mockCredit));
        console.log("CollateralVault deployed at:", address(vault));
        console.log("Owner / deployer:", deployer);
    }
}

/// @notice Deploys CollateralVault against the REAL Orbio CREDIT contract on
/// Robinhood Chain MAINNET (4663). Do NOT run this until DeployTestnet's
/// contract has been exercised end-to-end (lock, release, forfeit) with no
/// surprises. This script deploys the vault only — it does not move any of
/// your real CREDIT. Locking real CREDIT is a separate, explicit step (see
/// contracts/README.md) so a deploy failure never risks funds.
///
/// Usage:
///   forge script contracts/script/DeployMainnet.s.sol \
///     --rpc-url robinhood_mainnet --broadcast --private-key $PRIVATE_KEY
contract DeployMainnet is Script {
    /// Real Orbio CREDIT token address, Robinhood Chain mainnet (chain 4663).
    /// Source: https://www.orbio.so/protocol/agents (Contracts section).
    address constant ORBIO_CREDIT = 0xe33322Da1380e61E5AE5DFb21e7F62924c73004C;

    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        vm.startBroadcast(deployerKey);
        CollateralVault vault = new CollateralVault(ORBIO_CREDIT, deployer);
        vm.stopBroadcast();

        console.log("CollateralVault (mainnet, real CREDIT) deployed at:", address(vault));
        console.log("Owner / deployer:", deployer);
        console.log("CREDIT token:", ORBIO_CREDIT);
    }
}
