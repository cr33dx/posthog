import React, { useCallback, useState } from 'react'
import { Row, Spin, Button } from 'antd'
import { Table, Modal } from 'antd'
import { useValues, useActions } from 'kea'
import { invitesLogic } from './logic'
import { CreateOrgInviteModal } from './CreateOrgInviteModal'
import { DeleteOutlined, ExclamationCircleOutlined } from '@ant-design/icons'
import { humanFriendlyDetailedTime } from 'lib/utils'
import { hot } from 'react-hot-loader/root'
import { UserType } from '~/types'

export const Invites = hot(_Invites)
function _Invites({ user }: { user: UserType }): JSX.Element {
    const { invites, invitesLoading } = useValues(invitesLogic)
    const { deleteInvite } = useActions(invitesLogic)
    const [isCreateInviteModalVisible, setIsCreateInviteModalVisible] = useState(false)
    const { confirm } = Modal

    const ActionsComponent = useCallback(
        (_text, invite) => {
            function handleClick(): void {
                confirm({
                    title: `Delete invite?`,
                    icon: <ExclamationCircleOutlined />,
                    okText: 'Delete',
                    okType: 'danger',
                    cancelText: 'Cancel',
                    onOk() {
                        deleteInvite(invite)
                    },
                })
            }

            return (
                <div>
                    <a className="text-danger" onClick={handleClick}>
                        <DeleteOutlined />
                    </a>
                </div>
            )
        },
        [user]
    )

    const columns = [
        {
            title: 'ID',
            dataIndex: 'id',
            key: 'id',
        },
        {
            title: 'Uses So Far',
            dataIndex: 'uses',
            key: 'uses',
        },
        {
            title: 'Limit',
            dataIndex: 'limit',
            key: 'target_email',
            render: (target_email: number | null | undefined, invite: Record<string, any>) =>
                target_email ??
                (invite.max_uses ?? null === null
                    ? 'wildcard'
                    : `${invite.max_uses - invite.uses} ${invite.max_uses - invite.uses === 1 ? 'use' : 'uses'} left`),
        },
        {
            title: 'Created At',
            dataIndex: 'created_at',
            key: 'created_by',
            render: (createdAt: string) => humanFriendlyDetailedTime(createdAt),
        },
        {
            title: 'Last Used By',
            dataIndex: 'last_used_by_first_name',
            key: 'last_used_by',
            render: (lastUsedByFirstName: string, invite) =>
                invite.last_used_by_id ? `${lastUsedByFirstName} (${invite.last_used_by_email})` : 'no one yet',
        },
        {
            title: 'Created By',
            dataIndex: 'created_by_first_name',
            key: 'created_by',
            render: (createdByFirstName: string, invite) => `${createdByFirstName} (${invite.created_by_email})`,
        },
        {
            title: '',
            dataIndex: 'actions',
            key: 'actions',
            align: 'center',
            render: ActionsComponent,
        },
    ]

    return (
        <>
            <h1 className="page-header">Organization Invites – {user.organization.name}</h1>
            <div style={{ maxWidth: 672 }}>
                <i>
                    <p>Create, send out, and delete {user.organization.name} invites.</p>
                </i>
            </div>
            <Button
                type="primary"
                onClick={() => {
                    setIsCreateInviteModalVisible(true)
                }}
            >
                + Create an Invite
            </Button>
            <CreateOrgInviteModal isVisible={isCreateInviteModalVisible} setIsVisible={setIsCreateInviteModalVisible} />
            <div style={{ marginTop: '1rem' }}>
                {invitesLoading ? (
                    <Row justify="center">
                        <Spin />
                    </Row>
                ) : (
                    <Table dataSource={invites} columns={columns} rowKey="id" pagination={false} />
                )}
            </div>
        </>
    )
}
