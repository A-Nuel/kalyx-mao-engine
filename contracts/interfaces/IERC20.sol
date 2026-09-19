// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @dev Minimal ERC-20 interface subset — matches the standard `transfer` /
/// `transferFrom` calls documented in Orbio's own CREDIT ABI
/// (https://www.orbio.so/protocol/abi/credit.json), so CollateralVault works
/// against the real CREDIT contract on Robinhood Chain mainnet (4663) as-is,
/// with no changes, once verified against a mock on testnet.
interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);

    function transferFrom(address from, address to, uint256 amount) external returns (bool);

    function approve(address spender, uint256 amount) external returns (bool);

    function balanceOf(address account) external view returns (uint256);

    function allowance(address owner, address spender) external view returns (uint256);
}
