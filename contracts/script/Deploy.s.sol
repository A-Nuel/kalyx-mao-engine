// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {CollateralVault} from "../CollateralVault.sol";
import {MockCredit} from "../mocks/MockCredit.sol";

contract DeployTestnet is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        vm.startBroadcast(deployerKey);
        MockCredit mockCredit = new MockCredit();
        CollateralVault vault = new CollateralVault(address(mockCredit), deployer);
        mockCredit.mint(deployer, 1_000_000_000);
        vm.stopBroadcast();

        console.log("MockCredit deployed at:", address(mockCredit));
        console.log("CollateralVault deployed at:", address(vault));
        console.log("Owner / pledger:", deployer);
    }
}

contract DeployMainnet is Script {
    address constant ORBIO_CREDIT = 0xe33322Da1380e61E5AE5DFb21e7F62924c73004C;

    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        vm.startBroadcast(deployerKey);
        CollateralVault vault = new CollateralVault(ORBIO_CREDIT, deployer);
        vm.stopBroadcast();

        console.log("CollateralVault (real CREDIT) deployed at:", address(vault));
        console.log("Owner / settlement signer:", deployer);
        console.log("Initial pledger can be the same wallet for the first demo,");
        console.log("or a separate pledger wallet can be configured in Kalyx.");
        console.log("CREDIT token:", ORBIO_CREDIT);
    }
}
